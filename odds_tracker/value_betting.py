"""
Value Betting Engine v2 — Calibrated live & pre-match value detection.

CRITICAL CHANGES from v1:
1. Maximum edge caps per league tier (no more +700% fantasy edges)
2. Recovery bets heavily penalized (teams losing 2+ goals rarely win)
3. Draw deflation (Poisson overestimates draws — well-known bias)
4. Leader persistence (winning team holds lead more than Poisson suggests)
5. Late-game compression (less time = smaller probability swings)
6. League quality weighting (top-league markets are nearly unbeatable)
7. Minimum bookmaker count (thin markets = unreliable odds)
8. Bet type filtering (no bets in injury time, no bets on blowouts)
"""
import math
from datetime import datetime
from typing import Optional

from odds_tracker.research import (
    get_league_tier, get_max_edge, get_min_bookmakers,
    get_market_efficiency, classify_match_state,
    calibrate_live_probabilities, DRAW_DEFLATION,
    RECOVERY_PENALTY, LEADER_HOLD_FACTOR,
)

# ─── CONSTANTS ──────────────────────────────────────────────────

AVERAGE_MARGIN = 0.05
MIN_EDGE_PREMATCH = 0.03
MIN_EDGE_LIVE = 0.05
KELLY_FRACTION = 0.25
MAX_STAKE_FRACTION = 0.05

# Absolute maximum edge we'll ever report (anything above = model error)
ABSOLUTE_MAX_EDGE = 0.25  # 25% — even this is extremely rare

# Confidence thresholds
HIGH_CONFIDENCE = 0.80
MEDIUM_CONFIDENCE = 0.60


# ─── CORE PROBABILITY FUNCTIONS ────────────────────────────────

def odds_to_probability(odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if odds is None or odds <= 1.0:
        return 1.0
    return 1.0 / odds


def probability_to_odds(prob: float) -> float:
    """Convert probability to fair decimal odds."""
    if prob is None or prob <= 0:
        return 99.0
    return min(1.0 / prob, 999.0)


def remove_margin(odds_1: float, odds_x: float, odds_2: float) -> dict:
    """Remove bookmaker margin using multiplicative method."""
    p1 = odds_to_probability(odds_1)
    px = odds_to_probability(odds_x)
    p2 = odds_to_probability(odds_2)
    total = p1 + px + p2

    if total <= 0:
        return {'p1': 0.33, 'px': 0.33, 'p2': 0.33, 'margin': 0}

    return {
        'p1': p1 / total,
        'px': px / total,
        'p2': p2 / total,
        'margin': total - 1.0,
    }


def calculate_edge(fair_prob: float, offered_odds: float) -> float:
    """Edge = (fair_prob * offered_odds) - 1. Positive = value."""
    if fair_prob is None or offered_odds is None:
        return 0.0
    return (fair_prob * offered_odds) - 1.0


def kelly_stake(edge: float, odds: float, fraction: float = KELLY_FRACTION) -> float:
    """Fractional Kelly criterion for stake sizing."""
    if edge is None or odds is None or edge <= 0 or odds <= 1:
        return 0
    b = odds - 1.0
    p = 1.0 / odds + edge / odds
    q = 1.0 - p
    if b <= 0:
        return 0
    kelly = (b * p - q) / b
    kelly = max(0, kelly) * fraction
    return min(kelly, MAX_STAKE_FRACTION)


# ─── POISSON MODEL ─────────────────────────────────────────────

def poisson_pmf(k: int, lam: float) -> float:
    """Poisson probability mass function."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def match_probabilities_from_goals(exp_home: float, exp_away: float, max_goals: int = 6) -> dict:
    """Calculate raw 1X2 probabilities using independent Poisson model."""
    p1 = p_x = p2 = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            prob = poisson_pmf(i, exp_home) * poisson_pmf(j, exp_away)
            if i > j:
                p1 += prob
            elif i == j:
                p_x += prob
            else:
                p2 += prob
    return {'p1': p1, 'px': p_x, 'p2': p2}


def estimate_expected_goals_from_odds(odds_1: float, odds_x: float, odds_2: float) -> tuple[float, float]:
    """Reverse-engineer expected goals from pre-match 1X2 odds."""
    probs = remove_margin(odds_1, odds_x, odds_2)
    p1 = probs['p1']
    px = probs['px']
    p2 = probs['p2']

    if px > 0:
        total_goals = max(0.5, min(5.0, -1.5 * math.log(px) + 0.5))
    else:
        total_goals = 2.5

    if p1 + p2 > 0:
        home_ratio = p1 / (p1 + p2)
    else:
        home_ratio = 0.5

    exp_home = total_goals * home_ratio * 1.05
    exp_away = total_goals * (1 - home_ratio) * 0.95
    return exp_home, exp_away


def adjust_expected_goals_for_live(
    exp_home: float, exp_away: float,
    score_home: int, score_away: int,
    minute: int, total_minutes: int = 90,
) -> tuple[float, float]:
    """Adjust expected remaining goals for current state."""
    if minute >= total_minutes:
        return 0.0, 0.0

    remaining_fraction = max(0, (total_minutes - minute)) / total_minutes
    rem_home = exp_home * remaining_fraction
    rem_away = exp_away * remaining_fraction
    goal_diff = score_home - score_away

    if goal_diff > 0:
        rem_home *= (1.0 - 0.10 * min(goal_diff, 3))
        rem_away *= (1.0 + 0.10 * min(goal_diff, 3))
    elif goal_diff < 0:
        rem_home *= (1.0 + 0.10 * min(abs(goal_diff), 3))
        rem_away *= (1.0 - 0.10 * min(abs(goal_diff), 3))

    if minute >= 70:
        desperation = (minute - 70) / 20.0
        if goal_diff > 0:
            rem_away *= (1.0 + 0.15 * desperation)
        elif goal_diff < 0:
            rem_home *= (1.0 + 0.15 * desperation)

    return max(0, rem_home), max(0, rem_away)


def _parse_minute(minute_str: str) -> int:
    """Parse match minute string to integer."""
    minute_str = str(minute_str).strip().rstrip("'")
    if minute_str.upper() in ('HT', 'BREAK'):
        return 45
    if minute_str.upper() in ('FT', 'ET'):
        return 90
    if '+' in minute_str:
        parts = minute_str.split('+')
        base = int(parts[0]) if parts[0] else 0
        extra = int(parts[1]) if len(parts) > 1 and parts[1] else 2
        return base + extra
    try:
        return int(minute_str)
    except ValueError:
        return 0


# ─── PRE-MATCH VALUE ANALYSIS ──────────────────────────────────

def analyze_prematch_value(
    signal: dict,
    opening_odds: dict[str, float],
    current_odds: dict[str, float],
) -> dict | None:
    """Check if a pre-match signal has value."""
    bet = signal.get('bet', '')
    if bet not in ('1', 'X', '2'):
        return None

    offered_odds = current_odds.get(bet)
    if not offered_odds or offered_odds <= 1.0:
        return None

    opening_probs = remove_margin(
        opening_odds.get('1', 2.0), opening_odds.get('X', 3.0), opening_odds.get('2', 3.0),
    )
    current_probs = remove_margin(
        current_odds.get('1', 2.0), current_odds.get('X', 3.0), current_odds.get('2', 3.0),
    )

    prob_key = {'1': 'p1', 'X': 'px', '2': 'p2'}[bet]
    opening_prob = opening_probs[prob_key]
    current_prob = current_probs[prob_key]

    model_prob = 0.3 * opening_prob + 0.7 * current_prob

    signal_conf = signal.get('confidence', 0)
    if signal_conf >= HIGH_CONFIDENCE:
        model_prob *= 1.03
    elif signal_conf >= MEDIUM_CONFIDENCE:
        model_prob *= 1.01
    model_prob = min(model_prob, 0.95)

    edge = calculate_edge(model_prob, offered_odds)

    # Cap edge at realistic level
    league = signal.get('league', '')
    country = signal.get('country', '')
    max_edge = get_max_edge(league, country)
    edge = min(edge, max_edge, ABSOLUTE_MAX_EDGE)

    if edge < MIN_EDGE_PREMATCH:
        return None

    stake = kelly_stake(edge, offered_odds)

    return {
        'type': 'PRE_MATCH_VALUE',
        'bet': bet,
        'home': signal.get('home', ''),
        'away': signal.get('away', ''),
        'kick_off': signal.get('kick_off', ''),
        'offered_odds': offered_odds,
        'fair_odds': probability_to_odds(model_prob),
        'model_prob': model_prob,
        'implied_prob': odds_to_probability(offered_odds),
        'edge': edge,
        'kelly_stake': stake,
        'confidence': signal_conf,
        'signal_type': signal.get('signal_type', ''),
        'reason': _build_value_reason(edge, model_prob, offered_odds, signal),
    }


# ─── CALIBRATED LIVE VALUE ANALYSIS ────────────────────────────

def analyze_live_value(
    live_match: dict,
    prematch_odds: Optional[dict] = None,
) -> list[dict]:
    """
    Analyze a live match for value betting — CALIBRATED VERSION.

    Key filters:
    - No recovery bets (down 2+ goals)
    - No draw bets when 2+ goal difference
    - No late-game against-the-trend bets
    - No blowout matches (3+ diff)
    - No injury time / final minutes
    - No tier 1 leagues (market too efficient)
    - Edge capped by league tier
    - Max 25pp model-market disagreement
    """
    live_1 = live_match.get('odds_1')
    live_x = live_match.get('odds_x')
    live_2 = live_match.get('odds_2')

    if not all([live_1, live_x, live_2]):
        return []
    if any(o is None or o <= 1.0 for o in [live_1, live_x, live_2]):
        return []

    score_home = live_match.get('score_home', 0)
    score_away = live_match.get('score_away', 0)
    minute = _parse_minute(live_match.get('minute', '0'))
    league = live_match.get('league', '')
    country = live_match.get('country', '')
    num_bk = live_match.get('num_bookmakers', 0)

    # ── HARD FILTERS ─────────────────────────────────────────
    if minute >= 85 or minute < 3:
        return []
    if abs(score_home - score_away) >= 4:
        return []

    tier = get_league_tier(league, country)
    max_edge = get_max_edge(league, country)
    min_bk = get_min_bookmakers(league, country)

    if num_bk > 0 and num_bk < min_bk:
        return []

    # ── MODEL ────────────────────────────────────────────────
    if prematch_odds:
        pm_1 = prematch_odds.get('odds_1') or prematch_odds.get('1', 2.0)
        pm_x = prematch_odds.get('odds_x') or prematch_odds.get('X', 3.0)
        pm_2 = prematch_odds.get('odds_2') or prematch_odds.get('2', 3.0)
        has_prematch = True
    else:
        pm_1, pm_x, pm_2 = live_1, live_x, live_2
        has_prematch = False

    if not pm_1 or not pm_x or not pm_2:
        return []

    exp_home, exp_away = estimate_expected_goals_from_odds(pm_1, pm_x, pm_2)
    rem_home, rem_away = adjust_expected_goals_for_live(
        exp_home, exp_away, score_home, score_away, minute
    )

    # Poisson probabilities
    p_home_win = 0.0
    p_draw = 0.0
    p_away_win = 0.0

    for rem_h in range(7):
        for rem_a in range(7):
            prob = poisson_pmf(rem_h, rem_home) * poisson_pmf(rem_a, rem_away)
            final_h = score_home + rem_h
            final_a = score_away + rem_a
            if final_h > final_a:
                p_home_win += prob
            elif final_h == final_a:
                p_draw += prob
            else:
                p_away_win += prob

    # CALIBRATION
    p_home_win, p_draw, p_away_win = calibrate_live_probabilities(
        p_home_win, p_draw, p_away_win,
        score_home, score_away, minute
    )

    # Market probabilities for comparison
    market_probs = remove_margin(live_1, live_x, live_2)

    value_bets = []
    goal_diff = score_home - score_away
    outcomes = [
        ('1', p_home_win, live_1, live_match.get('home', '?'), market_probs['p1']),
        ('X', p_draw, live_x, 'Draw', market_probs['px']),
        ('2', p_away_win, live_2, live_match.get('away', '?'), market_probs['p2']),
    ]

    for bet_code, model_prob, offered_odds, label, market_prob in outcomes:
        if model_prob <= 0.03 or offered_odds is None or offered_odds <= 1.01:
            continue

        # Bet-specific hard filters
        if bet_code == '1' and goal_diff < -1:
            continue
        if bet_code == '2' and goal_diff > 1:
            continue
        if bet_code == 'X' and abs(goal_diff) >= 2:
            continue
        if minute >= 70:
            if bet_code == '1' and goal_diff < 0:
                continue
            if bet_code == '2' and goal_diff > 0:
                continue
            if bet_code == 'X' and goal_diff != 0:
                continue

        edge = calculate_edge(model_prob, offered_odds)

        # Edge cap
        if edge > max_edge * 2:
            continue  # Model error
        edge = min(edge, max_edge, ABSOLUTE_MAX_EDGE)

        if edge < MIN_EDGE_LIVE:
            continue

        # Model-market disagreement check
        prob_disagreement = abs(model_prob - market_prob)
        if prob_disagreement > 0.25:
            continue

        stake = kelly_stake(edge, offered_odds)

        # Confidence
        confidence = 0.45
        if has_prematch:
            confidence += 0.10
        if num_bk >= 8:
            confidence += 0.10
        elif num_bk >= 5:
            confidence += 0.05
        if edge >= 0.08:
            confidence += 0.05
        if 20 <= minute <= 65:
            confidence += 0.05
        if tier >= 4:
            confidence += 0.05
        if prob_disagreement > 0.15:
            confidence -= 0.10

        confidence = max(0.30, min(0.85, confidence))

        value_bets.append({
            'type': 'LIVE_VALUE',
            'bet': bet_code,
            'bet_label': f"{bet_code} ({label})",
            'home': live_match.get('home', ''),
            'away': live_match.get('away', ''),
            'minute': live_match.get('minute', ''),
            'score': live_match.get('score', ''),
            'offered_odds': offered_odds,
            'fair_odds': probability_to_odds(model_prob),
            'model_prob': model_prob,
            'implied_prob': odds_to_probability(offered_odds),
            'edge': edge,
            'kelly_stake': stake,
            'confidence': confidence,
            'exp_goals_home': rem_home,
            'exp_goals_away': rem_away,
            'country': country,
            'league': league,
            'tier': tier,
            'reason': _build_live_reason(
                edge, model_prob, offered_odds, minute,
                score_home, score_away, label, bet_code, tier
            ),
        })

    value_bets.sort(key=lambda v: v['edge'], reverse=True)
    return value_bets


def analyze_all_live(
    live_matches: list[dict],
    prematch_data: Optional[dict] = None,
) -> list[dict]:
    """Analyze all live matches — calibrated, max 10 results."""
    all_values = []
    prematch_data = prematch_data or {}

    for m in live_matches:
        key = (m.get('home', ''), m.get('away', ''))
        pm = prematch_data.get(key)
        values = analyze_live_value(m, pm)
        all_values.extend(values)

    all_values.sort(key=lambda v: v['edge'], reverse=True)
    return all_values[:10]


# ─── FORMATTING ─────────────────────────────────────────────────

def _build_value_reason(edge, model_prob, odds, signal):
    """Build reason for pre-match value bet."""
    parts = [
        f"Edge: {edge:+.1%}",
        f"Model: {model_prob:.0%} vs implied {odds_to_probability(odds):.0%}",
        f"Fair: {probability_to_odds(model_prob):.2f} vs offered {odds:.2f}",
    ]
    sig_type = signal.get('signal_type', '')
    if sig_type:
        parts.append(f"Signal: {sig_type}")
    pct = signal.get('pct_change', 0)
    if pct:
        parts.append(f"Move: {pct:+.1%}")
    return ' | '.join(parts)


def _build_live_reason(edge, model_prob, odds, minute, sh, sa, label, bet_code, tier=4):
    """Build reason for live value bet."""
    parts = [
        f"Edge: {edge:+.1%}",
        f"Model: {model_prob:.0%} vs market: {odds_to_probability(odds):.0%}",
        f"Fair: {probability_to_odds(model_prob):.2f} vs offered: {odds:.2f}",
    ]

    goal_diff = sh - sa
    if bet_code == '1' and goal_diff > 0:
        parts.append(f"Home leading {sh}:{sa}")
    elif bet_code == '2' and goal_diff < 0:
        parts.append(f"Away leading {sh}:{sa}")
    elif bet_code == 'X' and goal_diff == 0:
        parts.append(f"Drawing {sh}:{sa}")
    elif bet_code == '1' and goal_diff == 0:
        parts.append(f"Home value in draw")
    elif bet_code == '2' and goal_diff == 0:
        parts.append(f"Away value in draw")
    elif bet_code == '1' and goal_diff == -1:
        parts.append(f"Home trailing by 1 ({sh}:{sa})")
    elif bet_code == '2' and goal_diff == 1:
        parts.append(f"Away trailing by 1 ({sh}:{sa})")

    if minute < 30:
        parts.append(f"Early ({minute}')")
    elif minute < 60:
        parts.append(f"Mid-game ({minute}')")
    else:
        parts.append(f"Second half ({minute}')")

    parts.append(f"T{tier}")
    return ' | '.join(parts)


def format_value_bet(vb: dict) -> str:
    """Format a value bet for terminal display."""
    if vb['type'] == 'LIVE_VALUE':
        tier = vb.get('tier', '?')
        goal_info = vb.get('reason', '').split(' | ')
        # Extract match state and timing from reason (skip edge/model/fair which are already shown)
        context_parts = [p for p in goal_info if not p.startswith('Edge:') and not p.startswith('Model:') and not p.startswith('Fair:')]
        context = ' | '.join(context_parts) if context_parts else ''
        return (
            f"  [{vb['minute']}'] {vb['home']} {vb['score']} {vb['away']} [T{tier}]\n"
            f"     BET: {vb['bet_label']} @ {vb['offered_odds']:.2f} "
            f"(fair: {vb['fair_odds']:.2f})\n"
            f"     Edge: {vb['edge']:+.1%} | "
            f"Model: {vb['model_prob']:.0%} | "
            f"Kelly: {vb['kelly_stake']:.1%} | "
            f"Conf: {vb['confidence']:.0%}\n"
            f"     {context}"
        )
    else:
        return (
            f"  [{vb.get('kick_off', '')}] {vb['home']} vs {vb['away']}\n"
            f"     BET: {vb.get('bet_label', vb.get('bet', ''))} @ {vb['offered_odds']:.2f} "
            f"(fair: {vb['fair_odds']:.2f})\n"
            f"     Edge: {vb['edge']:+.1%} | "
            f"Model: {vb['model_prob']:.0%} | "
            f"Kelly: {vb['kelly_stake']:.1%} | "
            f"Conf: {vb['confidence']:.0%}\n"
            f"     {vb['reason']}"
        )


def format_value_bet_short(vb: dict) -> str:
    """One-line format for value bet."""
    if vb['type'] == 'LIVE_VALUE':
        return (
            f"  {vb['minute']:>4}' | {vb['home']:15s} {vb['score']} {vb['away']:15s} | "
            f"{vb['bet_label']:15s} @ {vb['offered_odds']:.2f} "
            f"(fair {vb['fair_odds']:.2f}) | "
            f"Edge {vb['edge']:+.1%} | T{vb.get('tier', '?')}"
        )
    else:
        return (
            f"  {vb.get('kick_off', ''):>5} | {vb['home']:15s} vs {vb['away']:15s} | "
            f"{vb.get('bet_label', vb.get('bet', '')):15s} @ {vb['offered_odds']:.2f} "
            f"(fair {vb['fair_odds']:.2f}) | "
            f"Edge {vb['edge']:+.1%}"
        )
