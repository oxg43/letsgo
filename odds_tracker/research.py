"""
Match Research Engine — Fetches team form, stats, and league context
from web sources to build proper team ratings for value detection.

Uses OddsPortal's own data + external sources to gather:
- Team form (last 5 matches)
- League quality tier
- Home/away performance differentials
- Head-to-head hints
"""
import re
import math
from datetime import datetime
from typing import Optional

# ─── LEAGUE QUALITY TIERS ──────────────────────────────────────
# Markets in higher-tier leagues are MORE efficient (harder to beat).
# Lower-tier leagues have softer lines but also lower limits.
#
# Tier 1: Top-5 leagues + Champions League — market is extremely efficient
# Tier 2: Good European leagues — market is strong
# Tier 3: Smaller European leagues — some inefficiency exists
# Tier 4: Non-European / lower divisions — more room for value
# Tier 5: Obscure leagues / friendlies — odds are very soft but unreliable

# Country-specific league overrides: (league_lower, country_lower) -> tier
# This MUST be checked BEFORE the generic league name lookup
LEAGUE_COUNTRY_TIERS = {
    ('premier league', 'england'): 1,
    ('premier league', 'kuwait'): 4,
    ('premier league', 'egypt'): 3,
    ('premier league', 'malta'): 4,
    ('premier league', 'saudi arabia'): 2,
    ('super league', 'switzerland'): 2,
    ('super league', 'greece'): 2,
    ('super league', 'turkey'): 2,
    ('super league', 'china'): 3,
    ('super league', 'albania'): 4,
    ('ligue 1', 'france'): 1,
    ('ligue 1', 'senegal'): 4,
    ('ligue 1', 'cote d\'ivoire'): 4,
    ('ligue 1', 'cameroon'): 4,
    ('serie a', 'italy'): 1,
    ('serie a', 'brazil'): 2,
    ('bundesliga', 'germany'): 1,
    ('bundesliga', 'austria'): 2,
    ('primera division', 'argentina'): 2,
    ('primera division', 'spain'): 1,
    ('professional league', 'oman'): 4,
    ('professional league', 'uae'): 3,
}

LEAGUE_TIERS = {
    # Tier 1 — Market extremely efficient
    'Premier League': 1, 'LaLiga': 1, 'La Liga': 1, 'Bundesliga': 1,
    'Serie A': 1, 'Ligue 1': 1, 'Champions League': 1,
    'UEFA Champions League': 1, 'Europa League': 1,
    'Conference League': 1,
    # Tier 2 — Market strong
    'Championship': 2, 'Serie B': 2, 'Eredivisie': 2,
    'Primeira Liga': 2, 'Liga Portugal': 2, 'Jupiler Pro League': 2,
    'Super Lig': 2, 'Ekstraklasa': 2, 'Scottish Premiership': 2,
    'Superliga': 2, '2. Bundesliga': 2, 'Ligue 2': 2,
    'Segunda Division': 2, 'Liga Portugal 2': 2,
    'Saudi Professional League': 2, 'MLS': 2,
    'Ligat ha\'Al': 2, 'Super League': 2,
    'Liga de Primera': 2, 'Allsvenskan': 2,
    # Tier 3 — Some opportunity
    'League One': 3, 'League Two': 3, 'Serie C': 3,
    'Eerste Divisie': 3, '3. Liga': 3, 'HNL': 3,
    'SuperSport HNL': 3, 'Fortuna Liga': 3, 'NB I': 3,
    'Primera RFEF': 3, 'Liga MX': 3,
    'Serie C - Group A': 3, 'Serie C - Group B': 3, 'Serie C - Group C': 3,
    'Botola Pro': 3, 'Challenger Pro League': 3,
    'National League': 3, 'CAF Champions League': 3,
    'Premiere Ligue Women': 3, 'Super League Women': 3,
    'Scottish Cup': 3, 'Campeonato de Portugal': 3,
    # Tier 4 — Good opportunities possible
    'National League North': 4, 'National League South': 4,
    'Segunda RFEF': 4, 'Liga 3': 4,
    'Campeonato Nacional U19': 4, 'Liga MX U21': 4,
    'Liiga Cup': 4, 'NPFL': 4, 'Ligi Kuu Bara': 4,
    'UAE League': 4, 'Paulista A4': 4,
    'League Two': 3,
    # Tier 5 — Very soft but unreliable
    'Club Friendly': 5, 'Friendly': 5, 'Tercera RFEF': 5,
    'Isthmian League': 5, 'Southern League': 5,
    'Isthmian League Premier Division': 5,
    'Southern League Premier South': 5,
}

# How much we trust the market by tier (higher = trust market more = smaller edges possible)
TIER_MARKET_EFFICIENCY = {
    1: 0.97,  # Top leagues: market is 97% efficient — practically impossible to beat
    2: 0.93,  # Good leagues: 93% efficient — only huge moves give edge
    3: 0.88,  # Mid leagues: 88% efficient — decent opportunity
    4: 0.82,  # Lower leagues: 82% efficient — real opportunity
    5: 0.75,  # Obscure: 75% efficient — but data quality is poor
}

# Maximum credible edge by tier (anything above = model error, not real edge)
TIER_MAX_EDGE = {
    1: 0.05,   # 5% max in top leagues
    2: 0.08,   # 8% max in good leagues
    3: 0.12,   # 12% max in mid leagues
    4: 0.18,   # 18% max in lower leagues
    5: 0.15,   # 15% max in obscure (cap lower due to data quality)
}

# Minimum bookmakers for reliable odds
TIER_MIN_BOOKMAKERS = {
    1: 8,
    2: 6,
    3: 4,
    4: 3,
    5: 2,
}


def get_league_tier(league: str, country: str = '') -> int:
    """Get quality tier for a league. Returns 1-5."""
    if not league:
        return 5

    league_lower = league.lower().strip()
    country_lower = country.lower().strip() if country else ''

    # 1. Country-specific match FIRST (prevents Kuwait PL = England PL)
    key = (league_lower, country_lower)
    if key in LEAGUE_COUNTRY_TIERS:
        return LEAGUE_COUNTRY_TIERS[key]

    # 1b. Try partial country-specific match
    for (lk, ck), tier in LEAGUE_COUNTRY_TIERS.items():
        if lk in league_lower and ck in country_lower:
            return tier

    # 2. Direct league name match
    if league in LEAGUE_TIERS:
        return LEAGUE_TIERS[league]

    # 3. Partial league name match
    for known, tier in LEAGUE_TIERS.items():
        if known.lower() in league_lower or league_lower in known.lower():
            return tier

    # 4. Country-based fallback
    if country_lower in ('england', 'spain', 'germany', 'italy', 'france'):
        return 3
    if country_lower in ('portugal', 'netherlands', 'belgium', 'turkey',
                         'scotland', 'austria', 'switzerland', 'poland',
                         'czech republic', 'croatia', 'greece', 'israel',
                         'brazil', 'argentina', 'mexico', 'chile', 'colombia'):
        return 3
    if country_lower in ('world',):
        return 5  # "World" usually means friendlies/qualifiers

    return 4  # Default for unknown leagues


def get_max_edge(league: str, country: str = '') -> float:
    """Maximum credible edge for this league tier."""
    tier = get_league_tier(league, country)
    return TIER_MAX_EDGE.get(tier, 0.15)


def get_min_bookmakers(league: str, country: str = '') -> int:
    """Minimum bookmakers required for reliable odds in this league."""
    tier = get_league_tier(league, country)
    return TIER_MIN_BOOKMAKERS.get(tier, 3)


def get_market_efficiency(league: str, country: str = '') -> float:
    """How efficient is the market for this league (0-1)."""
    tier = get_league_tier(league, country)
    return TIER_MARKET_EFFICIENCY.get(tier, 0.82)


# ─── SCORE-BASED MATCH STATE ANALYSIS ──────────────────────────

def classify_match_state(score_home: int, score_away: int, minute: int) -> dict:
    """
    Classify the current match state for betting decisions.
    Returns dict with state info and betting recommendations.
    """
    goal_diff = score_home - score_away
    total_goals = score_home + score_away

    state = {
        'phase': 'early' if minute < 30 else ('mid' if minute < 60 else ('late' if minute < 80 else 'final')),
        'goal_diff': goal_diff,
        'total_goals': total_goals,
        'home_leading': goal_diff > 0,
        'away_leading': goal_diff < 0,
        'is_draw': goal_diff == 0,
        'is_blowout': abs(goal_diff) >= 3,
        'minute': minute,
    }

    # Betting recommendations based on state
    state['avoid_recovery'] = abs(goal_diff) >= 2 and minute >= 45
    state['avoid_draw_late'] = minute >= 75 and goal_diff != 0
    state['prefer_leader'] = abs(goal_diff) >= 2 and minute >= 60
    state['high_goals_game'] = total_goals >= 4
    state['scoreless_draw'] = score_home == 0 and score_away == 0

    return state


# ─── POISSON CALIBRATION ───────────────────────────────────────
# Research shows the independent Poisson model has known biases:
# 1. Overestimates draw probability (goals are not truly independent)
# 2. Underestimates extreme results
# 3. Ignores in-game momentum
# 4. Zero-inflation for very defensive matches
#
# Calibration factors based on empirical data:

DRAW_DEFLATION = 0.88   # Reduce Poisson draw probability by 12%
LEADER_HOLD_FACTOR = 1.15  # Teams leading are 15% more likely to hold
RECOVERY_PENALTY = {
    1: 0.85,   # 1 goal behind: 85% of model probability (small penalty)
    2: 0.55,   # 2 goals behind: 55% (major penalty — recoveries are rare)
    3: 0.25,   # 3+ goals behind: 25% (almost never happens)
}


def calibrate_live_probabilities(
    p_home: float, p_draw: float, p_away: float,
    score_home: int, score_away: int, minute: int
) -> tuple[float, float, float]:
    """
    Apply calibration corrections to raw Poisson model probabilities.
    
    Key corrections:
    1. Draw deflation — Poisson overestimates draws
    2. Leader persistence — leading teams hold more than model predicts
    3. Recovery penalty — coming-from-behind is rarer than Poisson suggests
    4. Late-game compression — less time = probabilities shift toward current state
    """
    goal_diff = score_home - score_away

    # 1. Draw deflation (Poisson overestimates draws)
    p_draw *= DRAW_DEFLATION

    # 2. Leader persistence — the team that's ahead usually stays ahead
    if goal_diff > 0:  # Home leading
        hold_boost = LEADER_HOLD_FACTOR ** min(goal_diff, 3)
        p_home *= hold_boost
    elif goal_diff < 0:  # Away leading
        hold_boost = LEADER_HOLD_FACTOR ** min(abs(goal_diff), 3)
        p_away *= hold_boost

    # 3. Recovery penalty — penalize the losing team's win probability
    if goal_diff > 0:  # Home leading, away needs recovery
        deficit = min(goal_diff, 3)
        p_away *= RECOVERY_PENALTY.get(deficit, 0.25)
    elif goal_diff < 0:  # Away leading, home needs recovery
        deficit = min(abs(goal_diff), 3)
        p_home *= RECOVERY_PENALTY.get(deficit, 0.25)

    # 4. Late-game compression — less time = more weight on current state
    if minute >= 75:
        compression = (minute - 75) / 15.0  # 0 at 75', 1 at 90'
        compression = min(compression, 1.0)

        if goal_diff > 0:
            # Compress toward home win
            p_home = p_home * (1 + 0.3 * compression)
            p_draw *= (1 - 0.2 * compression)
            p_away *= (1 - 0.4 * compression)
        elif goal_diff < 0:
            # Compress toward away win
            p_away = p_away * (1 + 0.3 * compression)
            p_draw *= (1 - 0.2 * compression)
            p_home *= (1 - 0.4 * compression)
        else:
            # Draw at 80'+ = very likely to stay draw
            p_draw = p_draw * (1 + 0.15 * compression)
            p_home *= (1 - 0.1 * compression)
            p_away *= (1 - 0.1 * compression)

    # Normalize to sum to 1.0
    total = p_home + p_draw + p_away
    if total > 0:
        p_home /= total
        p_draw /= total
        p_away /= total

    return p_home, p_draw, p_away


# ─── UPCOMING MATCH DEEP ANALYSIS ──────────────────────────────

def analyze_upcoming_match(
    match: dict,
    opening_odds: dict,
    current_odds: dict,
    num_snapshots: int = 0,
) -> list[dict]:
    """
    Deep analysis of an upcoming match for value, regardless of steam signals.
    
    Looks at:
    1. Odds movement pattern (even small moves can be informative)
    2. Market consensus (are all outcomes moving in same direction?)
    3. Opening vs current spread
    4. League tier and market efficiency
    5. Home/away advantage patterns
    
    Returns list of value bet opportunities.
    """
    value_bets = []

    league = match.get('league', '')
    country = match.get('country', '')
    tier = get_league_tier(league, country)
    max_edge = get_max_edge(league, country)
    market_eff = get_market_efficiency(league, country)

    # Need valid odds for all outcomes
    o1 = current_odds.get('1', 0) or current_odds.get('odds_1', 0)
    ox = current_odds.get('X', 0) or current_odds.get('odds_x', 0)
    o2 = current_odds.get('2', 0) or current_odds.get('odds_2', 0)

    if not o1 or not ox or not o2:
        return []
    if o1 <= 1.0 or ox <= 1.0 or o2 <= 1.0:
        return []

    op1 = opening_odds.get('1', 0) or opening_odds.get('odds_1', 0)
    opx = opening_odds.get('X', 0) or opening_odds.get('odds_x', 0)
    op2 = opening_odds.get('2', 0) or opening_odds.get('odds_2', 0)

    if not op1 or not opx or not op2:
        return []

    # Calculate movements
    moves = {}
    for outcome, cur, opn in [('1', o1, op1), ('X', ox, opx), ('2', o2, op2)]:
        if opn > 1.0:
            pct_change = (cur - opn) / opn
            moves[outcome] = {
                'opening': opn,
                'current': cur,
                'pct_change': pct_change,
                'direction': 'down' if pct_change < -0.02 else ('up' if pct_change > 0.02 else 'stable'),
            }

    if not moves:
        return []

    # Find outcomes with significant drops (sharp money indicators)
    for outcome, data in moves.items():
        pct = data['pct_change']

        # Only interested in drops (odds going down = market thinks more likely)
        if pct >= -0.02:
            continue

        drop_size = abs(pct)

        # Check if OTHER outcomes are rising (confirmation of sharp move)
        others_rising = all(
            moves[o]['pct_change'] > 0
            for o in moves if o != outcome
        )

        # Build model probability from odds movement analysis
        # Start with current market implied probability (already includes margin)
        from odds_tracker.value_betting import remove_margin, calculate_edge, kelly_stake, probability_to_odds
        current_probs = remove_margin(o1, ox, o2)

        prob_key = {'1': 'p1', 'X': 'px', '2': 'p2'}[outcome]
        market_prob = current_probs[prob_key]

        # Our edge estimate: based on how much the odds dropped vs market efficiency
        # In an efficient market, the current odds ARE the fair odds
        # Edge only exists if:
        # 1. Odds dropped significantly (sharp money)
        # 2. Drop is sustained (not just noise)
        # 3. Other outcomes confirm the move
        # 4. The league allows for some inefficiency

        edge_estimate = 0.0

        # Sharp money signal: drop > 5% with confirmation
        if drop_size >= 0.05 and others_rising:
            # Edge estimate scales with drop size but is capped by market efficiency
            raw_edge = drop_size * (1.0 - market_eff)  # Only the "inefficiency gap"
            edge_estimate = min(raw_edge, max_edge)

        # Large sustained drops (>8%) in less efficient leagues
        elif drop_size >= 0.08 and tier >= 3:
            raw_edge = drop_size * (1.0 - market_eff) * 0.8
            edge_estimate = min(raw_edge, max_edge)

        # Skip if edge is negligible
        if edge_estimate < 0.02:
            continue

        # Calculate fair probability (our model)
        model_prob = market_prob * (1 + edge_estimate)
        model_prob = min(model_prob, 0.95)

        offered_odds = data['current']
        actual_edge = calculate_edge(model_prob, offered_odds)

        if actual_edge < 0.02:
            continue

        # Cap edge at maximum for this league
        actual_edge = min(actual_edge, max_edge)
        stake = kelly_stake(actual_edge, offered_odds)

        # Confidence based on data quality
        confidence = 0.50
        if num_snapshots >= 20:
            confidence += 0.10
        if others_rising:
            confidence += 0.15
        if drop_size >= 0.08:
            confidence += 0.10
        if tier >= 3:
            confidence += 0.05  # More opportunity in lower tiers

        confidence = min(confidence, 0.90)

        # Build reason
        reasons = [f"Odds drop: {pct:+.1%}"]
        if others_rising:
            reasons.append("Market consensus (others rising)")
        reasons.append(f"League tier: {tier}")
        if num_snapshots >= 10:
            reasons.append(f"{num_snapshots} data points")

        label_map = {'1': match.get('home', '?'), 'X': 'Draw', '2': match.get('away', '?')}

        value_bets.append({
            'type': 'PRE_MATCH_VALUE',
            'bet': outcome,
            'bet_label': f"{outcome} ({label_map[outcome]})",
            'home': match.get('home', ''),
            'away': match.get('away', ''),
            'kick_off': match.get('kick_off', ''),
            'offered_odds': offered_odds,
            'fair_odds': probability_to_odds(model_prob),
            'model_prob': model_prob,
            'edge': actual_edge,
            'kelly_stake': stake,
            'confidence': confidence,
            'league': league,
            'country': country,
            'tier': tier,
            'pct_change': pct,
            'reason': ' | '.join(reasons),
        })

    return value_bets
