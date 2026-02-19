"""
Signal Generator v2 — Tier-based betting signal system.

╔══════════════════════════════════════════════════════════════════════╗
║  BAZIRAN NA ANALIZI: 1,750 utakmica │ 943,637 redova │ 4 dana      ║
║  TIER 1 (STRONG): Domaćin Drop ≥10-20%, odds <3.00, 0-30min       ║
║  TIER 2 (MEDIUM): Domaćin Drop ≥5%, Remi Drop ≥10-15%             ║
║  TIER 3 (WEAK):   Gost jaki favorit Drop ≥10%, odds <1.60         ║
║  WATCH  (tier=0):  >30 min do početka — samo praćenje              ║
╚══════════════════════════════════════════════════════════════════════╝

Signal Flow:
    1. Za svaku upcoming utakmicu → analyze_match() iz baze
    2. Za svaki ishod (1, X, 2) → izračunaj drop %
    3. Provjeri anti-filtere → blokiraj ako je uvjet ispunjen
    4. Matchaj tier pravila (T1 → T2 → T3) → prvi match pobjeđuje
    5. Ako >30 min → WATCH (tier=0, praćenje bez uloga)
    6. Izračunaj stake → Kelly ¼ prema tieru i pravilima
    7. Generiraj signal dict s tier oznakom, stake preporukom, razlogom
"""
from datetime import datetime, timedelta

from odds_tracker.analyzer import analyze_match
from odds_tracker.database import save_signal, get_match_history
from odds_tracker.signal_config import (
    ALL_TIER_RULES, ANTI_FILTERS, EXCLUDED_LEAGUES,
    TIER_NAMES, TIER_EMOJIS, STAKING,
    get_odds_segment, get_tier_name, get_tier_emoji,
)
from odds_tracker.staking import calculate_stake, BankrollManager


# ═══════════════════════════════════════════════════════════════════
#  CORE: Minutes to Kickoff
# ═══════════════════════════════════════════════════════════════════

def _compute_minutes_to_ko(match: dict, now: datetime = None) -> float | None:
    """
    Compute minutes until kickoff, handling today's and future matches.

    Returns None if kickoff time cannot be determined.
    Returns negative values if match already started.
    """
    now = now or datetime.now()
    ko_str = match.get('kick_off', '')
    if not ko_str:
        return None

    # Try with explicit match_date first (for future/tomorrow matches)
    match_date = match.get('_match_date', '')
    if match_date:
        try:
            ko_dt = datetime.strptime(f"{match_date} {ko_str}", '%Y-%m-%d %H:%M')
            return (ko_dt - now).total_seconds() / 60
        except ValueError:
            pass

    # Fallback: try today's date
    today_str = now.strftime('%Y-%m-%d')
    try:
        ko_dt = datetime.strptime(f"{today_str} {ko_str}", '%Y-%m-%d %H:%M')
        return (ko_dt - now).total_seconds() / 60
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════
#  CORE: Drop % Calculation
# ═══════════════════════════════════════════════════════════════════

def _compute_drop_pct(opening_odds: float, closing_odds: float) -> float:
    """
    Calculate drop percentage.

    drop_pct = (opening - closing) / opening * 100
    Positive = odds FELL (money entered on this outcome = signal).
    Negative = odds ROSE (money left this outcome).

    Example: 2.20 -> 1.85 = +15.9% drop (bullish signal)
    Example: 1.80 -> 2.10 = -16.7% rise (bearish)
    """
    if not opening_odds or opening_odds <= 1.0:
        return 0.0
    return (opening_odds - closing_odds) / opening_odds * 100


# ═══════════════════════════════════════════════════════════════════
#  ANTI-FILTER CHECK
# ═══════════════════════════════════════════════════════════════════

def _check_anti_filters(
    outcome: str,
    drop_pct: float,
    closing_odds: float,
    minutes_to_ko: float,
    snapshots: int,
    league: str = '',
) -> tuple[bool, str]:
    """
    Check if a signal should be BLOCKED by anti-filters.
    
    Updated 2026-02-18: Added banned_odds_ranges and banned_drop_ranges
    based on professional analysis. Confidence filter is separate (after
    confidence is computed).

    Returns (is_blocked, reason).
    """
    # League blacklist
    if league in EXCLUDED_LEAGUES:
        return True, f'Liga iskljucena: {league}'

    # Global filters
    g = ANTI_FILTERS.get('global', {})
    if snapshots < g.get('min_snapshots', 3):
        return True, f'Premalo snapshotova: {snapshots} < {g["min_snapshots"]}'

    # Outcome-specific filters
    of = ANTI_FILTERS.get(outcome, {})

    if 'max_closing_odds' in of and closing_odds >= of['max_closing_odds']:
        return True, (
            f'{outcome} odds {closing_odds:.2f} >= {of["max_closing_odds"]:.2f} '
            f'(autsajderi - negativan ROI)'
        )

    if 'min_drop_pct' in of and drop_pct < of['min_drop_pct']:
        return True, (
            f'{outcome} drop {drop_pct:.1f}% < min {of["min_drop_pct"]:.1f}% '
            f'(nedovoljan signal)'
        )
    
    # NEW: Banned odds ranges (e.g., 1.30-1.50 loses -29% edge)
    for lo, hi in of.get('banned_odds_ranges', []):
        if lo <= closing_odds < hi:
            return True, (
                f'{outcome} odds {closing_odds:.2f} u zabranjenom rasponu [{lo:.2f}-{hi:.2f}] '
                f'(negativan edge u analizi)'
            )
    
    # NEW: Banned drop % ranges (e.g., 6-10% loses -34.5% ROI)
    for lo, hi in of.get('banned_drop_ranges', []):
        if lo <= drop_pct < hi:
            return True, (
                f'{outcome} drop {drop_pct:.1f}% u zabranjenom rasponu [{lo:.0f}%-{hi:.0f}%] '
                f'(middle drops gube -34.5% ROI)'
            )

    return False, ''


# ═══════════════════════════════════════════════════════════════════
#  TIER MATCHING
# ═══════════════════════════════════════════════════════════════════

def _match_tier_rules(
    outcome: str,
    drop_pct: float,
    closing_odds: float,
    minutes_to_ko: float,
    snapshots: int,
) -> tuple[int | None, dict | None]:
    """
    Match against tier rules, in priority order (T1 -> T2 -> T3).
    First match wins.

    Returns (tier, rule_dict) or (None, None) if no match.
    """
    for tier, rule in ALL_TIER_RULES:
        if rule['outcome'] != outcome:
            continue

        if drop_pct < rule['min_drop_pct']:
            continue

        min_odds, max_odds = rule['odds_range']
        if closing_odds < min_odds or closing_odds >= max_odds:
            continue

        if minutes_to_ko > rule.get('max_minutes_to_ko', 30):
            continue

        if snapshots < rule.get('min_snapshots', 3):
            continue

        return tier, rule

    return None, None


def _match_watch_rules(
    outcome: str,
    drop_pct: float,
    closing_odds: float,
    snapshots: int,
) -> dict | None:
    """
    Check if outcome qualifies as WATCH signal (>30 min from KO).
    Uses the same tier rules but ignores time window constraint.

    Returns the matching rule dict (without tier) or None.
    """
    for _, rule in ALL_TIER_RULES:
        if rule['outcome'] != outcome:
            continue
        if drop_pct < rule['min_drop_pct']:
            continue
        min_odds, max_odds = rule['odds_range']
        if closing_odds < min_odds or closing_odds >= max_odds:
            continue
        if snapshots < rule.get('min_snapshots', 3):
            continue
        # Ignore max_minutes_to_ko -- this is WATCH
        return rule
    return None


# ═══════════════════════════════════════════════════════════════════
#  SIGNAL EVALUATION (per outcome)
# ═══════════════════════════════════════════════════════════════════

def _evaluate_outcome(analysis: dict, outcome: str, match: dict) -> dict | None:
    """
    Evaluate if there's a betting signal for a specific outcome.

    This function implements the full tier-based signal system:
    1. Compute drop %
    2. Check anti-filters
    3. Match tier rules
    4. Calculate stake
    5. Build signal dict

    Returns a signal dict or None.
    Backward-compatible with existing callers.
    """
    now = datetime.now()

    # -- Extract odds data from analysis --
    opening_odds = analysis['opening_odds'].get(outcome)
    latest_odds = analysis['latest_odds'].get(outcome)

    if not opening_odds or not latest_odds:
        return None
    if opening_odds <= 1.0 or latest_odds <= 1.0:
        return None

    # -- Compute drop % --
    drop_pct = _compute_drop_pct(opening_odds, latest_odds)

    # -- Minutes to kickoff --
    minutes_to_ko = _compute_minutes_to_ko(match, now)
    if minutes_to_ko is None:
        minutes_to_ko = 999  # Unknown -> treat as far away
    if minutes_to_ko < -2:
        return None  # Already started

    # -- Snapshots --
    snapshots = analysis.get('snapshots', 0)

    # -- League --
    league = match.get('league', '')

    # -- Anti-filter check --
    blocked, block_reason = _check_anti_filters(
        outcome, drop_pct, latest_odds, minutes_to_ko, snapshots, league
    )
    if blocked:
        return None

    # -- Tier matching --
    tier = None
    rule = None

    if minutes_to_ko <= ANTI_FILTERS.get('global', {}).get('max_minutes_to_ko', 30):
        # Within actionable window -> match real tiers
        tier, rule = _match_tier_rules(outcome, drop_pct, latest_odds, minutes_to_ko, snapshots)
    else:
        # Outside window -> check WATCH eligibility
        watch_rule = _match_watch_rules(outcome, drop_pct, latest_odds, snapshots)
        if watch_rule:
            tier = 0  # WATCH
            rule = watch_rule

    if tier is None:
        return None

    # -- Detect signal type (movement pattern) --
    signal_type = 'DROP_SIGNAL'
    if analysis.get('late_sharp') == outcome:
        signal_type = 'LATE_SHARP'
    elif analysis.get('steam_move') == outcome:
        signal_type = 'STEAM'
    elif drop_pct >= 5.0:
        signal_type = 'SUSTAINED_DROP'

    # -- Compute confidence (for backward compat + ZLATNA) --
    confidence = _compute_confidence(analysis, outcome, drop_pct, minutes_to_ko, snapshots, tier)

    # -- Confidence filter (NEW 2026-02-18: <80% loses -17% to -43% ROI) --
    min_conf = ANTI_FILTERS.get('global', {}).get('min_confidence', 0.0)
    if min_conf > 0 and confidence < min_conf:
        # Log blocked signal for tracking
        # print(f"[BLOCKED] {outcome} confidence {confidence:.0%} < {min_conf:.0%}")
        return None

    # -- Calculate stake --
    bankroll = STAKING['initial_bankroll']
    try:
        mgr = BankrollManager()
        bankroll = mgr.bankroll
    except Exception:
        pass

    if tier > 0:
        stake_info = calculate_stake(tier, latest_odds, rule, bankroll)
    else:
        # WATCH -> no stake
        stake_info = {
            'stake_amount': 0.0, 'stake_pct': 0.0,
            'kelly_full_pct': 0.0, 'kelly_used_pct': 0.0,
            'method': 'watch', 'hit_rate_used': 0.0,
        }

    # -- Odds segment --
    odds_segment = get_odds_segment(latest_odds)

    # -- Build reason string --
    reasons = _build_reasons(
        outcome, drop_pct, opening_odds, latest_odds,
        tier, rule, signal_type, minutes_to_ko, snapshots, odds_segment,
    )

    # -- Market consensus check --
    other_outcomes = [o for o in ['1', 'X', '2'] if o != outcome]
    others_rising = all(
        analysis.get(f'pct_change_{o.lower()}', 0) > 0.01
        for o in other_outcomes
    )

    # -- Trend --
    pct_key = f'pct_change_{outcome.lower()}'
    pct_change = analysis.get(pct_key, 0)
    trend_key = f'trend_{outcome.lower()}'
    trend = analysis.get(trend_key, 'unknown')

    # -- Build signal dict --
    signal = {
        # Core identification
        'home': analysis.get('home', match.get('home', '')),
        'away': analysis.get('away', match.get('away', '')),
        'kick_off': analysis.get('kick_off', match.get('kick_off', '')),
        'bet': outcome,

        # Tier system (NEW)
        'tier': tier,
        'tier_name': get_tier_name(tier),
        'tier_rule': rule.get('id', 'unknown') if rule else 'unknown',
        'tier_label': rule.get('label', '') if rule else '',

        # Drop data
        'drop_pct': round(drop_pct, 1),
        'minutes_to_ko': round(minutes_to_ko, 0),
        'odds_segment': odds_segment,

        # Movement pattern
        'signal_type': signal_type,
        'trend': trend,
        'pct_change': pct_change,
        'market_consensus': others_rising,

        # Odds
        'opening_odds': opening_odds,
        'latest_odds': latest_odds,
        'odds_at_signal': latest_odds,

        # Quality metrics
        'confidence': round(confidence, 2),
        'snapshots': snapshots,

        # Staking
        'stake_pct': stake_info['stake_pct'],
        'stake_amount': stake_info['stake_amount'],
        'kelly_full_pct': stake_info['kelly_full_pct'],
        'kelly_used_pct': stake_info['kelly_used_pct'],
        'staking_method': stake_info['method'],

        # Metadata
        'reason': ' | '.join(reasons),
        'league': league,
        'country': match.get('country', ''),
    }

    return signal


def _compute_confidence(
    analysis: dict, outcome: str,
    drop_pct: float, minutes_to_ko: float,
    snapshots: int, tier: int,
) -> float:
    """
    Compute a confidence score (0-1) for backward compatibility.

    The tier system is the primary classifier now, but existing modules
    (ZLATNA, signal_map) still use numerical confidence.

    Mapping:
        TIER 1 -> base 0.85-0.95
        TIER 2 -> base 0.70-0.85
        TIER 3 -> base 0.55-0.70
        WATCH  -> base 0.30-0.55
    """
    if tier == 0:
        base = 0.40
    elif tier == 1:
        base = 0.88
    elif tier == 2:
        base = 0.75
    elif tier == 3:
        base = 0.60
    else:
        base = 0.30

    # Bonuses
    bonuses = 0.0

    # Bigger drop -> more confident
    if drop_pct >= 20:
        bonuses += 0.05
    elif drop_pct >= 15:
        bonuses += 0.03
    elif drop_pct >= 10:
        bonuses += 0.02

    # Close to kickoff -> more confident
    if 5 <= minutes_to_ko <= 15:
        bonuses += 0.03
    elif 15 < minutes_to_ko <= 30:
        bonuses += 0.01

    # Many snapshots -> robust
    if snapshots >= 50:
        bonuses += 0.02
    elif snapshots >= 30:
        bonuses += 0.01

    # Market consensus
    if analysis.get('steam_move') == outcome:
        bonuses += 0.02
    if analysis.get('late_sharp') == outcome:
        bonuses += 0.03

    return min(base + bonuses, 0.95)


def _build_reasons(
    outcome: str, drop_pct: float,
    opening: float, closing: float,
    tier: int, rule: dict,
    signal_type: str, minutes_to_ko: float,
    snapshots: int, odds_segment: str,
) -> list[str]:
    """Build list of reason strings for the signal."""
    reasons = []

    # Tier identification
    tier_name = get_tier_name(tier)
    if tier > 0:
        reasons.append(f'{tier_name}')
    else:
        reasons.append('WATCH -- ceka prozor 0-30min')

    # Rule label
    if rule and rule.get('label'):
        reasons.append(rule['label'])

    # Drop info
    reasons.append(f'Drop: {opening:.2f} -> {closing:.2f} ({drop_pct:+.1f}%)')

    # Segment info
    segment_labels = {
        'jaki_favoriti': 'Jaki favorit',
        'umjereni_favoriti': 'Umjereni favorit',
        'srednje_kvote': 'Srednje kvote',
        'autsajderi': 'Autsajder',
    }
    reasons.append(segment_labels.get(odds_segment, odds_segment))

    # Time
    if minutes_to_ko <= 30:
        reasons.append(f'{minutes_to_ko:.0f}min do KO')
    else:
        reasons.append(f'{minutes_to_ko:.0f}min do KO (watch)')

    # Movement type
    if signal_type in ('LATE_SHARP', 'STEAM'):
        reasons.append(f'{signal_type} detektiran')

    # Data quality
    reasons.append(f'{snapshots} snapshotova')

    return reasons


# ═══════════════════════════════════════════════════════════════════
#  MAIN GENERATORS
# ═══════════════════════════════════════════════════════════════════

def generate_signals(matches: list[dict]) -> list[dict]:
    """
    Generate tier-based betting signals for all upcoming matches.

    Returns list of signal dicts sorted by:
        1. Tier (1 first, then 2, then 3, then 0/WATCH)
        2. Drop % (highest first within same tier)

    Backward-compatible with existing callers (runner.py, paper_trade_alerts.py).
    """
    signals = []
    now = datetime.now()
    seen_matches = set()  # Enforce max 1 bet per match
    seen_watch = set()    # Deduplicate WATCH signals per match+outcome

    # Deduplicate input matches by (home, away) — keep earliest _match_date
    deduped = {}
    for m in matches:
        home = m.get('home', m.get('home_team', ''))
        away = m.get('away', m.get('away_team', ''))
        key = f"{home}|{away}"
        if key not in deduped:
            deduped[key] = m
        else:
            # Keep the entry with the earliest _match_date (closest to actual match)
            existing_date = deduped[key].get('_match_date', '9999-99-99')
            new_date = m.get('_match_date', '9999-99-99')
            if new_date < existing_date:
                deduped[key] = m

    for m in deduped.values():
        if m.get('status') in ('finished', 'live'):
            continue

        home = m.get('home', m.get('home_team', ''))
        away = m.get('away', m.get('away_team', ''))
        ko = m.get('kick_off', '')

        if not home or not away:
            continue

        # Skip already started matches
        minutes = _compute_minutes_to_ko(m, now)
        if minutes is not None and minutes < -2:
            continue

        match_key = f"{home}|{away}"

        analysis = analyze_match(home, away, ko)

        if analysis['snapshots'] < 2:
            continue

        # Check each outcome
        for outcome in ['1', 'X', '2']:
            signal = _evaluate_outcome(analysis, outcome, m)
            if signal:
                # Enforce max 1 actionable bet per match
                if signal['tier'] > 0 and match_key in seen_matches:
                    existing = [s for s in signals
                                if f"{s['home']}|{s['away']}" == match_key and s['tier'] > 0]
                    if existing:
                        ex = existing[0]
                        if signal['tier'] < ex['tier'] or (
                            signal['tier'] == ex['tier'] and signal['drop_pct'] > ex['drop_pct']
                        ):
                            signals.remove(ex)
                            signals.append(signal)
                            seen_matches.add(match_key)
                    continue

                # Deduplicate WATCH signals per match+outcome
                if signal['tier'] == 0:
                    watch_key = f"{match_key}|{outcome}"
                    if watch_key in seen_watch:
                        continue
                    seen_watch.add(watch_key)

                if signal['tier'] > 0:
                    seen_matches.add(match_key)
                signals.append(signal)

    # Sort: tier ascending (T1 first), then drop % descending
    signals.sort(key=lambda s: (
        s['tier'] if s['tier'] > 0 else 99,  # WATCH at the end
        -s['drop_pct'],
    ))

    return signals


def generate_actionable_signals(matches: list[dict]) -> list[dict]:
    """
    Generate only ACTIONABLE signals (tier 1-3, within 0-30 min window).
    Excludes WATCH signals.
    """
    all_signals = generate_signals(matches)
    return [s for s in all_signals if s['tier'] > 0]


def generate_watch_signals(matches: list[dict]) -> list[dict]:
    """
    Generate only WATCH signals (potential signals >30 min from KO).
    """
    all_signals = generate_signals(matches)
    return [s for s in all_signals if s['tier'] == 0]


# ═══════════════════════════════════════════════════════════════════
#  FORMATTING
# ═══════════════════════════════════════════════════════════════════

def format_signal(s: dict) -> str:
    """Format a tier-based signal for terminal display."""
    tier = s.get('tier', 0)
    emoji = get_tier_emoji(tier)
    tier_name = get_tier_name(tier)

    bet_label = {
        '1': f"1 ({s['home']})",
        'X': 'X (Remi)',
        '2': f"2 ({s['away']})",
    }.get(s['bet'], s['bet'])

    # Header line
    lines = [
        f"{emoji} [{tier_name}] {s['kick_off']} | {s['home']} vs {s['away']}"
    ]

    # Bet details
    lines.append(
        f"   BET: {bet_label} @ {s['latest_odds']:.2f}"
    )

    # Drop info
    lines.append(
        f"   Drop: {s['opening_odds']:.2f} -> {s['latest_odds']:.2f} "
        f"({s['drop_pct']:+.1f}%) | {s['snapshots']} snaps | "
        f"{s.get('minutes_to_ko', 0):.0f}min do KO"
    )

    # Stake (only for actionable tiers)
    if tier > 0:
        lines.append(
            f"   Stake: {s['stake_pct']:.1f}% bankrolla ({s['stake_amount']:.2f}EUR) "
            f"| Kelly full: {s['kelly_full_pct']:.1f}% | Pravilo: {s.get('tier_rule', '?')}"
        )
    else:
        lines.append(
            f"   WATCH -- pracenje, ceka prozor 0-30min | "
            f"Pravilo: {s.get('tier_rule', '?')}"
        )

    # Consensus
    if s.get('market_consensus'):
        lines.append(f"   Market consensus -- ostali ishodi rastu")

    # Signal type
    if s.get('signal_type') in ('LATE_SHARP', 'STEAM'):
        lines.append(f"   {s['signal_type']} detektiran!")

    return '\n'.join(lines)


def format_signal_short(s: dict) -> str:
    """One-line compact signal format."""
    emoji = get_tier_emoji(s.get('tier', 0))
    tier = s.get('tier', 0)
    tier_tag = f'T{tier}' if tier > 0 else 'W'
    bet_label = {
        '1': f"1({s['home'][:12]})",
        'X': 'X(Remi)',
        '2': f"2({s['away'][:12]})",
    }.get(s['bet'], s['bet'])

    consensus = 'Y' if s.get('market_consensus') else ' '

    return (
        f"{emoji} [{tier_tag}] {s['kick_off']:>5} "
        f"{s['home'][:15]:15} vs {s['away'][:15]:15} | "
        f"{bet_label:20} @ {s['latest_odds']:.2f} | "
        f"Drop: {s['drop_pct']:+.1f}% | "
        f"Stake: {s['stake_pct']:.1f}% | "
        f"{s.get('minutes_to_ko', 0):.0f}min {consensus}"
    )


# ═══════════════════════════════════════════════════════════════════
#  DATABASE SAVE
# ═══════════════════════════════════════════════════════════════════

def save_signals_to_db(signals: list[dict]):
    """Save all generated signals to the database."""
    for s in signals:
        save_signal(s)


def get_top_signals(signals: list[dict], n: int = 10) -> list[dict]:
    """Get top N signals by tier then drop %."""
    return signals[:n]
