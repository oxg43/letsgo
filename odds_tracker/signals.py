"""
Signal Generator — produces actionable betting signals 
based on odds movement analysis.

Strategy hierarchy (strongest to weakest):
1. LATE SHARP  — Sharp movement 5-30 min before kick-off (highest confidence)
2. STEAM       — Sustained drop > 5% across multiple snapshots
3. CONVERGENCE — All bookmakers moving same direction
4. VALUE       — Odds higher than implied probability suggests
5. CONTRARIAN  — Reverse line movement against public
"""
from datetime import datetime, timedelta
from odds_tracker.analyzer import analyze_match, analyze_all_upcoming
from odds_tracker.database import save_signal, get_match_history
from odds_tracker.config import STEAM_MOVE_THRESHOLD, STRONG_SIGNAL_THRESHOLD


def generate_signals(matches: list[dict]) -> list[dict]:
    """
    Generate betting signals for all upcoming matches.
    Returns list of signal dicts sorted by confidence (highest first).
    """
    signals = []
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')

    for m in matches:
        if m.get('status') in ('finished', 'live'):
            continue

        # Skip matches that already started
        ko_str = m.get('kick_off', '')
        if ko_str:
            try:
                ko_dt = datetime.strptime(f"{today_str} {ko_str}", '%Y-%m-%d %H:%M')
                if (ko_dt - now).total_seconds() / 60 < -2:
                    continue
            except ValueError:
                pass

        analysis = analyze_match(m['home'], m['away'], ko_str)

        if analysis['snapshots'] < 2:
            continue

        # Check each outcome for signals
        for outcome in ['1', 'X', '2']:
            signal = _evaluate_outcome(analysis, outcome, m)
            if signal:
                signals.append(signal)

    # Sort by confidence
    signals.sort(key=lambda s: s['confidence'], reverse=True)
    return signals


def _evaluate_outcome(analysis: dict, outcome: str, match: dict) -> dict | None:
    """
    Evaluate if there's a betting signal for a specific outcome.
    Returns a signal dict or None.
    """
    trend_key = f'trend_{outcome.lower()}'
    pct_key = f'pct_change_{outcome.lower()}'

    trend = analysis.get(trend_key, 'unknown')
    pct_change = analysis.get(pct_key, 0)
    latest_odds = analysis['latest_odds'].get(outcome)
    opening_odds = analysis['opening_odds'].get(outcome)

    if not latest_odds or not opening_odds:
        return None

    confidence = 0.0
    reasons = []
    signal_type = ''

    # ─── 1. LATE SHARP DETECTION ───
    if analysis.get('late_sharp') == outcome:
        confidence += 0.35
        reasons.append(f'Late sharp move detected on {outcome}')
        signal_type = 'LATE_SHARP'

    # ─── 2. STEAM MOVE ───
    if analysis.get('steam_move') == outcome:
        confidence += 0.30
        reasons.append(f'Steam move: odds dropped {pct_change:+.1%}')
        if not signal_type:
            signal_type = 'STEAM'

    # ─── 3. SUSTAINED DROP ───
    if pct_change <= -STEAM_MOVE_THRESHOLD:
        confidence += 0.20
        reasons.append(f'Sustained odds drop: {opening_odds:.2f} → {latest_odds:.2f} ({pct_change:+.1%})')
        if not signal_type:
            signal_type = 'SUSTAINED_DROP'

    # ─── 4. STRONG DROP ───
    if pct_change <= -STRONG_SIGNAL_THRESHOLD:
        confidence += 0.15
        reasons.append(f'Strong drop >{STRONG_SIGNAL_THRESHOLD*100:.0f}%')

    # ─── 5. CONSISTENCY CHECK ───
    # If odds for this outcome dropping while others rising = strong signal
    other_outcomes = [o for o in ['1', 'X', '2'] if o != outcome]
    others_rising = all(
        analysis.get(f'pct_change_{o.lower()}', 0) > 0.01
        for o in other_outcomes
    )
    if others_rising and pct_change < -0.02:
        confidence += 0.15
        reasons.append('Other outcomes rising while this drops — market consensus')

    # ─── 6. PROXIMITY TO KICK-OFF BONUS ───
    if analysis.get('kick_off'):
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            ko = datetime.strptime(f"{today} {analysis['kick_off']}", '%Y-%m-%d %H:%M')
            minutes_to_ko = (ko - datetime.now()).total_seconds() / 60
            if minutes_to_ko < 0:
                return None  # Match already started — no pre-match signal
            elif 5 <= minutes_to_ko <= 30:
                confidence += 0.10
                reasons.append(f'Only {minutes_to_ko:.0f} min to kick-off — late data is more reliable')
            elif 0 <= minutes_to_ko < 5:
                confidence += 0.05
                reasons.append(f'Very close to kick-off ({minutes_to_ko:.0f} min)')
        except Exception:
            pass

    # ─── 7. MULTIPLE DATA POINTS BONUS ───
    if analysis['snapshots'] >= 5:
        confidence += 0.05
        reasons.append(f'{analysis["snapshots"]} snapshots — robust data')

    # Minimum confidence threshold to generate a signal
    if confidence < 0.25:
        return None

    # Cap confidence at 0.95
    confidence = min(confidence, 0.95)

    signal = {
        'home': analysis['home'],
        'away': analysis['away'],
        'kick_off': analysis.get('kick_off', ''),
        'signal_type': signal_type or 'MIXED',
        'bet': outcome,
        'confidence': round(confidence, 2),
        'odds_at_signal': latest_odds,
        'reason': ' | '.join(reasons),
        'opening_odds': opening_odds,
        'latest_odds': latest_odds,
        'pct_change': pct_change,
        'trend': trend,
        'snapshots': analysis['snapshots'],
    }

    return signal


def format_signal(s: dict) -> str:
    """Format a signal for display."""
    emoji = {
        'LATE_SHARP': '⚡',
        'STEAM': '🔥',
        'SUSTAINED_DROP': '📉',
        'MIXED': '📊',
    }
    icon = emoji.get(s['signal_type'], '📊')
    bet_label = {
        '1': f"1 ({s['home']})",
        'X': 'X (Draw)',
        '2': f"2 ({s['away']})",
    }

    lines = [
        f"{icon} [{s['signal_type']}] {s['kick_off']} | {s['home']} vs {s['away']}",
        f"   BET: {bet_label.get(s['bet'], s['bet'])} @ {s['latest_odds']:.2f}",
        f"   Confidence: {s['confidence']:.0%} | Odds: {s['opening_odds']:.2f} → {s['latest_odds']:.2f} ({s['pct_change']:+.1%})",
        f"   {s['reason']}",
    ]
    return '\n'.join(lines)


def save_signals_to_db(signals: list[dict]):
    """Save all generated signals to the database."""
    for s in signals:
        save_signal(s)


def get_top_signals(signals: list[dict], n: int = 10) -> list[dict]:
    """Get top N signals by confidence."""
    return signals[:n]
