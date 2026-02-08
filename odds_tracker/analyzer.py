"""
Odds Movement Analyzer — tracks how odds change over time and detects market trends.

Key concepts:
- STEAM MOVE:  Sharp, sustained movement in one direction (smart money)
- LATE SHARP:  Significant last-minute move (informed bettors)
- REVERSE LINE MOVEMENT: Odds move against public expectation
- DRIFT:  Gradual odds increase (money leaving that outcome)
- VALUE:  Odds higher than expected given market movement
"""
from datetime import datetime, timedelta
from odds_tracker.database import get_match_history
from odds_tracker.config import (
    STEAM_MOVE_THRESHOLD,
    STRONG_SIGNAL_THRESHOLD,
    MIN_SNAPSHOTS_FOR_SIGNAL,
    KEY_INTERVALS,
)


def analyze_match(home: str, away: str, kick_off: str = '') -> dict:
    """
    Analyze odds movement for a single match.
    Returns dict with:
        - trend_1, trend_x, trend_2: direction of movement ('down', 'up', 'stable')
        - pct_change_1, pct_change_x, pct_change_2: percentage change from first to last
        - steam_move: which outcome has steam ('1', 'X', '2', or None)
        - late_sharp: which outcome has late sharp move
        - snapshots: number of data points
        - intervals: dict of odds at key intervals before kick-off
        - recommendation: suggested bet or None
    """
    history = get_match_history(home, away)

    result = {
        'home': home,
        'away': away,
        'kick_off': kick_off,
        'snapshots': len(history),
        'trend_1': 'unknown', 'trend_x': 'unknown', 'trend_2': 'unknown',
        'pct_change_1': 0, 'pct_change_x': 0, 'pct_change_2': 0,
        'steam_move': None,
        'late_sharp': None,
        'latest_odds': {'1': None, 'X': None, '2': None},
        'opening_odds': {'1': None, 'X': None, '2': None},
        'intervals': {},
        'recommendation': None,
        'confidence': 0.0,
        'reason': '',
        'history': []
    }

    if len(history) < MIN_SNAPSHOTS_FOR_SIGNAL:
        result['reason'] = f'Nedovoljno podataka ({len(history)} snapshot-a, min {MIN_SNAPSHOTS_FOR_SIGNAL})'
        return result

    # Extract odds timeseries
    odds_series = {
        '1': [(h['scraped_at'], h['odds_1']) for h in history if h.get('odds_1')],
        'X': [(h['scraped_at'], h['odds_x']) for h in history if h.get('odds_x')],
        '2': [(h['scraped_at'], h['odds_2']) for h in history if h.get('odds_2')],
    }

    for outcome in ['1', 'X', '2']:
        series = odds_series[outcome]
        if len(series) < 2:
            continue

        first_val = series[0][1]
        last_val = series[-1][1]

        # Store opening and latest
        result['opening_odds'][outcome] = first_val
        result['latest_odds'][outcome] = last_val

        # Calculate percentage change
        if first_val > 0:
            pct = (last_val - first_val) / first_val
        else:
            pct = 0

        key_trend = f'trend_{outcome.lower().replace("x", "x")}'
        key_pct = f'pct_change_{outcome.lower().replace("x", "x")}'

        # Fix key names for 1, X, 2
        trend_key = 'trend_' + outcome.lower().replace('x', 'x')
        pct_key = 'pct_change_' + outcome.lower().replace('x', 'x')

        # Determine trend
        if pct < -0.02:
            result[trend_key] = 'down'  # odds dropping = more money coming in
        elif pct > 0.02:
            result[trend_key] = 'up'    # odds rising = money leaving
        else:
            result[trend_key] = 'stable'

        result[pct_key] = round(pct, 4)

        # Detect steam move (>5% drop)
        if pct <= -STEAM_MOVE_THRESHOLD:
            result['steam_move'] = outcome

        # Detect late sharp move (last 2 snapshots show significant movement)
        if len(series) >= 2:
            second_last = series[-2][1]
            if second_last > 0:
                late_pct = (last_val - second_last) / second_last
                if late_pct <= -0.03:  # 3% drop in last interval
                    result['late_sharp'] = outcome

    # Build history summary for display
    result['history'] = [
        {
            'time': h['scraped_at'],
            'odds_1': h.get('odds_1'),
            'odds_x': h.get('odds_x'),
            'odds_2': h.get('odds_2'),
        }
        for h in history
    ]

    # Calculate key interval snapshots
    if kick_off:
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            ko_time = datetime.strptime(f"{today} {kick_off}", '%Y-%m-%d %H:%M')
            for interval_min in KEY_INTERVALS:
                target_time = ko_time - timedelta(minutes=interval_min)
                # Find closest snapshot to this target time
                closest = None
                closest_diff = float('inf')
                for h in history:
                    h_time = datetime.fromisoformat(h['scraped_at'])
                    diff = abs((h_time - target_time).total_seconds())
                    if diff < closest_diff:
                        closest_diff = diff
                        closest = h
                if closest and closest_diff < 600:  # within 10 min of target
                    result['intervals'][f'{interval_min}min'] = {
                        'odds_1': closest.get('odds_1'),
                        'odds_x': closest.get('odds_x'),
                        'odds_2': closest.get('odds_2'),
                        'scraped_at': closest['scraped_at'],
                        'diff_seconds': closest_diff,
                    }
        except Exception:
            pass

    return result


def analyze_all_upcoming(matches: list[dict]) -> list[dict]:
    """
    Analyze all upcoming matches and return analysis results sorted by confidence.
    """
    results = []
    for m in matches:
        if m.get('status') in ('finished', 'live'):
            continue
        analysis = analyze_match(m['home'], m['away'], m.get('kick_off', ''))
        results.append(analysis)

    # Sort by absolute maximum percentage change (most volatile first)
    results.sort(key=lambda r: max(
        abs(r.get('pct_change_1', 0)),
        abs(r.get('pct_change_x', 0)),
        abs(r.get('pct_change_2', 0))
    ), reverse=True)

    return results


def get_movement_summary(analysis: dict) -> str:
    """Generate a human-readable movement summary for a match."""
    lines = []
    home = analysis['home']
    away = analysis['away']
    ko = analysis.get('kick_off', '??')

    lines.append(f"=== {ko} | {home} vs {away} ({analysis['snapshots']} snapshots) ===")

    for outcome, label in [('1', f'1 ({home})'), ('X', 'X (Draw)'), ('2', f'2 ({away})')]:
        trend_key = f'trend_{outcome.lower()}'
        pct_key = f'pct_change_{outcome.lower()}'
        opening = analysis['opening_odds'].get(outcome, '?')
        latest = analysis['latest_odds'].get(outcome, '?')
        trend = analysis.get(trend_key, '?')
        pct = analysis.get(pct_key, 0)

        direction = '↓' if trend == 'down' else '↑' if trend == 'up' else '→'
        lines.append(f"  {label}: {opening} → {latest} ({direction} {pct:+.1%})")

    if analysis.get('steam_move'):
        lines.append(f"  🔥 STEAM MOVE on {analysis['steam_move']}")
    if analysis.get('late_sharp'):
        lines.append(f"  ⚡ LATE SHARP on {analysis['late_sharp']}")

    if analysis.get('recommendation'):
        lines.append(f"  ➤ SIGNAL: Bet {analysis['recommendation']} (confidence: {analysis['confidence']:.0%})")
        lines.append(f"    Reason: {analysis['reason']}")

    return '\n'.join(lines)
