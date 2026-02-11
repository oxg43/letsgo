"""
Signal Map — Quad-file signal tracking system.

Four files are maintained:

1. LIVE_SIGNALS_<date>.tsv  — Rewritten EVERY cycle with ALL current signals.
   Always up to date. Open this file for the latest state.

2. FINAL_BETS_<date>.tsv    — Append-only. Signals captured 5-9 min before KO.
   This is the "golden window" file for actual betting decisions.

3. NOVCI_<date>.tsv — Append-only. BEST signals only (9-5 min before KO).
   Filtered by tier system (optimised from 1,750-match deep analysis):
   - TIER 1-3 qualified signals only
   - All tier-valid types (STEAM, LATE_SHARP, SUSTAINED_DROP, DROP_SIGNAL)
   - Confidence >= 75% (tiers encode quality)
   - Drop >= 5% (tier rules enforce proper thresholds)
   - Draws ALLOWED if tier-qualified (T2_DRAW rules)
   - Max odds 3.50
   - Score-ranked by strength + tier bonus

4. WEEKLY_SIGNALS_<date>.tsv — Append-only. Early-detection signals for future
   matches (tomorrow + 2-7 days ahead). Same NOVCI filters applied but
   without golden-window (5-9 min) restriction = catches early market moves.
"""
import csv
from datetime import datetime
from pathlib import Path

from odds_tracker.config import DATA_DIR

SIGNAL_MAP_DIR = DATA_DIR / "signal_map"
SIGNAL_MAP_DIR.mkdir(exist_ok=True)

# NOVCI window: 0-29 minutes before kick-off
GOLDEN_MIN = 0
GOLDEN_MAX = 29

TSV_COLUMNS = [
    'kick_off',
    'min_to_ko',
    'type',
    'match',
    'bet',
    'odds',
    'confidence',
    'change',
    'opening',
    'edge',
    'fair_odds',
    'kelly',
    'reason',
]

FINAL_COLUMNS = [
    'captured_at',
    'match_id',
    'kick_off',
    'kickoff_utc',
    'min_to_ko',
    'type',
    'match',
    'bet',
    'odds',
    'confidence',
    'change',
    'opening',
    'drop_from_open',
    'drop_last_60',
    'retrace_last_30',
    'snapshots',
    'snapshots_last_60',
    'consensus_flag',
    'steam_flag',
    'market_margin',
    'edge',
    'fair_odds',
    'kelly',
    'reason',
]

# ─── NOVCI columns ─────────────────────────────────────────────
NOVCI_COLUMNS = [
    'captured_at',
    'match_id',
    'kick_off',
    'kickoff_utc',
    'min_to_ko',
    'match',
    'bet',
    'odds',
    'opening',
    'drop_pct',
    'drop_from_open',
    'drop_last_60',
    'retrace_last_30',
    'snapshots',
    'snapshots_last_60',
    'confidence',
    'consensus_flag',
    'steam_flag',
    'market_margin',
    'score',
    'type',
    'reason',
]

# ─── NOVCI RULES ───────────────────────────────────────────────────
#
# Updated 2026-02-10 after deep analysis of 1,750 matches.
#
# Deep analysis (943,637 rows, 1,750 matches):
#   TIER 1: Home ≥10% drop → 44.4% hit, +76.8% ROI (N=108)
#   TIER 2: Home ≥5% drop → 46.1% hit, +52.1% ROI (N=245)
#   TIER 2: Draw ≥10% (2.20-3.00) → 27.1% hit, +41.8% ROI (N=48)
#   TIER 3: Away favorite ≥10% → jaki favoriti only
#   Time window: ONLY 0-30 min has positive ROI (+3.7%)
#
# NOVCI uses TIER SYSTEM as primary filter:
#   - TIER 1 or TIER 2 signals pass automatically if in golden window
#   - TIER 3 requires additional validation
#   - Confidence ≥ 75% (tiers encode signal quality)
#   - Drop ≥ 5% (tier rules enforce proper thresholds)
#   - Draws ALLOWED if tier-qualified (T2_DRAW rules have odds filters)
#
NOVCI_MIN_CONFIDENCE = 0.75      # Tiers encode signal quality
NOVCI_MIN_DROP_PCT = 0.05        # 5% min - tier rules enforce proper thresholds
NOVCI_MIN_SNAPSHOTS = 3          # Match tier min_snapshots
NOVCI_ALLOWED_TYPES = {'STEAM', 'LATE_SHARP', 'SUSTAINED_DROP', 'DROP_SIGNAL'}  # All tier-qualified types
NOVCI_MAX_ODDS = 3.50            # Above 3.50 is still bad
NOVCI_EXCLUDE_DRAWS = False      # Draws ALLOWED if tier-qualified (T2_DRAW rules filter properly)


def _live_file() -> Path:
    """Current live signals file (overwritten every cycle)."""
    return SIGNAL_MAP_DIR / f"LIVE_SIGNALS_{datetime.now().strftime('%Y-%m-%d')}.tsv"


def _final_file() -> Path:
    """Golden window bets file (append-only)."""
    return SIGNAL_MAP_DIR / f"FINAL_BETS_{datetime.now().strftime('%Y-%m-%d')}.tsv"


def _novci_file() -> Path:
    """NOVCI file — best tier-qualified bets (append-only)."""
    return SIGNAL_MAP_DIR / f"NOVCI_{datetime.now().strftime('%Y-%m-%d')}.tsv"


def _weekly_file() -> Path:
    """Weekly signals file — early detection for future matches (append-only)."""
    return SIGNAL_MAP_DIR / f"WEEKLY_SIGNALS_{datetime.now().strftime('%Y-%m-%d')}.tsv"


# ─── WEEKLY EARLY DETECTION thresholds (relaxed for early capture) ──
WEEKLY_EARLY_MIN_SNAPSHOTS = 5      # 5 snaps minimum for early tier
WEEKLY_EARLY_MIN_CONFIDENCE = 0.65   # 65%+ for early tier (vs 90% for confirmed)

# Weekly signal columns (same as NOVCI + match_date + days_until + stage)
WEEKLY_COLUMNS = [
    'captured_at',
    'match_id',
    'match_date',
    'days_until',
    'kick_off',
    'kickoff_utc',
    'match',
    'bet',
    'odds',
    'opening',
    'drop_pct',
    'drop_from_open',
    'snapshots',
    'snapshots_last_60',
    'confidence',
    'consensus_flag',
    'steam_flag',
    'market_margin',
    'score',
    'type',
    'stage',
    'reason',
]


def _bet_label(signal: dict) -> str:
    """Human-readable bet label from signal dict."""
    bet = signal.get('bet', '')
    home = signal.get('home', '?')
    away = signal.get('away', '?')
    if bet == '1':
        return f"1 ({home})"
    elif bet == 'X':
        return 'X (Draw)'
    elif bet == '2':
        return f"2 ({away})"
    return bet


def _signal_to_row(s: dict, now: datetime) -> dict | None:
    """Convert a signal dict to a TSV row. Returns None if kick_off is invalid."""
    match_date = s.get('_match_date', '') or now.strftime('%Y-%m-%d')
    ko_str = s.get('kick_off', '')
    if not ko_str:
        return None

    try:
        ko_dt = datetime.strptime(f"{match_date} {ko_str}", '%Y-%m-%d %H:%M')
        minutes_to_ko = (ko_dt - now).total_seconds() / 60
    except ValueError:
        return None

    # Skip matches that already started (more than 5 min ago)
    if minutes_to_ko < -5:
        return None

    # Build tier tag if available
    tier = s.get('tier', '')
    tier_rule = s.get('tier_rule', '')
    sig_type = s.get('signal_type', 'SIGNAL')
    if tier and tier > 0:
        sig_type = f'T{tier}:{sig_type}'

    return {
        'kick_off': ko_str,
        'match_id': s.get('match_id', ''),
        'kickoff_utc': s.get('kickoff_utc', ''),
        'min_to_ko': int(max(0, minutes_to_ko)),
        'type': sig_type,
        'match': f"{s.get('home', '?')} vs {s.get('away', '?')}",
        'bet': _bet_label(s),
        'odds': round(s.get('latest_odds', 0), 3),
        'confidence': round(s.get('confidence', 0), 3),
        'change': round(s.get('pct_change', 0), 4),
        'opening': round(s.get('opening_odds', 0), 3),
        'drop_from_open': round(s.get('drop_from_open', 0), 4),
        'drop_last_60': round(s.get('drop_last_60', 0), 4),
        'retrace_last_30': round(s.get('retrace_last_30', 0), 4),
        'snapshots': s.get('snapshots', 0),
        'snapshots_last_60': s.get('snapshots_last_60', 0),
        'consensus_flag': s.get('consensus_flag', 0),
        'steam_flag': s.get('steam_flag', 0),
        'market_margin': round(s.get('market_margin', 0), 4),
        'edge': '',
        'fair_odds': '',
        'kelly': round(s.get('kelly_full_pct', 0), 2) if s.get('kelly_full_pct') else '',
        'reason': s.get('reason', ''),
    }


# ─── LIVE SIGNALS (rewritten every cycle) ───────────────────────

def write_live_signals(signals: list[dict], value_bets: list[dict] = None) -> int:
    """
    Write ALL current signals to LIVE_SIGNALS.tsv (overwrites completely).
    Merges regular signals with value bets for enriched output.
    Returns number of signals written.
    """
    now = datetime.now()
    tsv_path = _live_file()

    # Build a lookup of value bets by (home, away, bet)
    vb_lookup = {}
    if value_bets:
        for vb in value_bets:
            key = (vb.get('home', ''), vb.get('away', ''), vb.get('bet', ''))
            vb_lookup[key] = vb

    rows = []
    for s in signals:
        row = _signal_to_row(s, now)
        if row:
            # Enrich with value bet data if available
            key = (s.get('home', ''), s.get('away', ''), s.get('bet', ''))
            vb = vb_lookup.get(key)
            if vb:
                row['edge'] = f"{vb.get('edge', 0):+.1%}"
                row['fair_odds'] = f"{vb.get('fair_odds', 0):.2f}"
                row['kelly'] = f"{vb.get('kelly_stake', 0):.1%}"
                row['type'] = f"VALUE+{row['type']}"
            rows.append(row)

    # Sort by kick_off time, then by confidence descending
    rows.sort(key=lambda r: (r['kick_off'], -float(r['confidence'])))

    # Overwrite the file completely
    with open(tsv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=TSV_COLUMNS, delimiter='\t',
                                extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return len(rows)


# ─── FINAL BETS (append-only, golden window 5-9 min) ───────────

def _load_final_keys(path: Path) -> set:
    """Load existing keys from FINAL_BETS to avoid duplicates."""
    keys = set()
    if not path.exists():
        return keys
    try:
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                key = f"{row.get('kick_off', '')}|{row.get('match', '')}|{row.get('bet', '')}"
                keys.add(key)
    except Exception:
        pass
    return keys


def capture_final_bets(signals: list[dict], value_bets: list[dict] = None) -> list[dict]:
    """
    Capture signals in the golden window (5-9 min before kick-off)
    and APPEND them to FINAL_BETS.tsv.
    Returns list of newly captured signal rows.
    """
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')

    # Build value bet lookup
    vb_lookup = {}
    if value_bets:
        for vb in value_bets:
            key = (vb.get('home', ''), vb.get('away', ''), vb.get('bet', ''))
            vb_lookup[key] = vb

    golden = []
    for s in signals:
        ko_str = s.get('kick_off', '')
        if not ko_str:
            continue
        match_date = s.get('_match_date', '') or today_str
        try:
            ko_dt = datetime.strptime(f"{match_date} {ko_str}", '%Y-%m-%d %H:%M')
            minutes_to_ko = (ko_dt - now).total_seconds() / 60
        except ValueError:
            continue

        if GOLDEN_MIN <= minutes_to_ko <= GOLDEN_MAX:
            row = _signal_to_row(s, now)
            if row:
                row['captured_at'] = now.strftime('%H:%M:%S')
                # Enrich with value data
                key = (s.get('home', ''), s.get('away', ''), s.get('bet', ''))
                vb = vb_lookup.get(key)
                if vb:
                    row['edge'] = f"{vb.get('edge', 0):+.1%}"
                    row['fair_odds'] = f"{vb.get('fair_odds', 0):.2f}"
                    row['kelly'] = f"{vb.get('kelly_stake', 0):.1%}"
                    row['type'] = f"VALUE+{row['type']}"
                golden.append(row)

    if not golden:
        return []

    tsv_path = _final_file()
    existing_keys = _load_final_keys(tsv_path)

    file_exists = tsv_path.exists()
    new_rows = []

    with open(tsv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FINAL_COLUMNS, delimiter='\t',
                                extrasaction='ignore')
        if not file_exists:
            writer.writeheader()

        for row in golden:
            key = f"{row['kick_off']}|{row['match']}|{row['bet']}"
            if key not in existing_keys:
                existing_keys.add(key)
                writer.writerow(row)
                new_rows.append(row)

    return new_rows


# ─── NOVCI (append-only, tier-qualified bets 9-5 min) ──────────

def _calculate_novci_score(pct_drop: float, confidence: float, snapshots: int,
                           has_consensus: bool) -> float:
    """
    Calculate quality score for a NOVCI signal.
    Formula derived from today's analysis of 74,500+ snapshots.

    Score components:
    - Drop strength (40%): bigger drop = sharper money = stronger signal
    - Confidence (30%): higher = more signal factors agree
    - Data robustness (20%): more snapshots = more reliable
    - Market consensus (10%): all other outcomes rising = market agrees

    Score range: 0.0 to 100.0
    """
    import math
    drop_score = min(abs(pct_drop) / 0.25, 1.0) * 40    # max at 25% drop
    conf_score = confidence * 30                           # 0.80-0.95 → 24-28.5
    data_score = min(math.sqrt(snapshots) / 10, 1.0) * 20 # sqrt(30)=5.5, sqrt(80)=8.9
    cons_score = 10.0 if has_consensus else 0.0

    return round(drop_score + conf_score + data_score + cons_score, 1)


def capture_novci(signals: list[dict], value_bets: list[dict] = None) -> list[dict]:
    """
    Capture ONLY the highest-quality signals in the golden window (9-5 min
    before kick-off) using tier-based filtering.

    Rules:
    1. Tier 1-3 qualified (WATCH/tier 0 excluded)
    2. STEAM, LATE_SHARP, SUSTAINED_DROP, or DROP_SIGNAL type
    3. Confidence >= 75%
    4. Drop >= 5%
    5. Min 3 snapshots
    6. Draws allowed if tier-qualified
    7. Max odds 3.50
    8. T3 requires ≥15% drop
    9. Score-ranked with tier bonus

    Returns list of newly captured NOVCI rows.
    """
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')

    # Build value bet lookup
    vb_lookup = {}
    if value_bets:
        for vb in value_bets:
            key = (vb.get('home', ''), vb.get('away', ''), vb.get('bet', ''))
            vb_lookup[key] = vb

    candidates = []
    for s in signals:
        ko_str = s.get('kick_off', '')
        if not ko_str:
            continue
        match_date = s.get('_match_date', '') or today_str
        try:
            ko_dt = datetime.strptime(f"{match_date} {ko_str}", '%Y-%m-%d %H:%M')
            minutes_to_ko = (ko_dt - now).total_seconds() / 60
        except ValueError:
            continue

        # Must be in golden window: 5-9 minutes before kickoff
        if not (GOLDEN_MIN <= minutes_to_ko <= GOLDEN_MAX):
            continue

        # ── TIER-BASED NOVCI FILTERS ──

        tier = s.get('tier', 0)
        confidence = s.get('confidence', 0)
        sig_type = s.get('signal_type', '')
        pct_change = s.get('pct_change', 0)
        snapshots = s.get('snapshots', 0)
        bet = s.get('bet', '')
        latest_odds = s.get('latest_odds', 0)
        drop_pct = s.get('drop_pct', 0)

        # Rule 1: Must be tier-qualified (TIER 1 or 2 pass easily, T3 needs validation)
        if tier == 0:
            continue  # WATCH signals never go to NOVCI

        # Rule 2: Signal type check (now includes all tier-valid types)
        if sig_type not in NOVCI_ALLOWED_TYPES:
            continue

        # Rule 3: Minimum confidence (now lower since tiers encode quality)
        if confidence < NOVCI_MIN_CONFIDENCE:
            continue

        # Rule 4: Minimum drop %
        if abs(pct_change) < NOVCI_MIN_DROP_PCT and drop_pct < (NOVCI_MIN_DROP_PCT * 100):
            continue

        # Rule 5: Minimum snapshots
        if snapshots < NOVCI_MIN_SNAPSHOTS:
            continue

        # Rule 6: Draw filtering — now allowed if tier-qualified
        # T2_DRAW rules already filter by odds range, so NOVCI trusts the tier
        if NOVCI_EXCLUDE_DRAWS and bet == 'X':
            continue

        # Rule 7: Max odds cap
        if latest_odds > NOVCI_MAX_ODDS:
            continue

        # Rule 8: TIER 3 extra validation — require higher drop for NOVCI inclusion
        if tier == 3 and drop_pct < 15.0:
            continue  # T3 needs at least 15% drop for NOVCI quality

        # ── PASSED ALL FILTERS — calculate score ──
        reason = s.get('reason', '')
        has_consensus = bool(s.get('consensus_flag', 0)) or s.get('market_consensus', False)

        # Tier-boosted score: T1 gets 10pt bonus, T2 gets 5pt, T3 gets 0
        tier_bonus = {1: 10.0, 2: 5.0, 3: 0.0}.get(tier, 0.0)
        base_score = _calculate_novci_score(pct_change, confidence, snapshots, has_consensus)
        score = base_score + tier_bonus

        row = {
            'captured_at': now.strftime('%H:%M:%S'),
            'match_id': s.get('match_id', ''),
            'kick_off': ko_str,
            'kickoff_utc': s.get('kickoff_utc', ''),
            'min_to_ko': int(minutes_to_ko),
            'match': f"{s.get('home', '?')} vs {s.get('away', '?')}",
            'bet': _bet_label(s),
            'odds': round(latest_odds, 3),
            'opening': round(s.get('opening_odds', 0), 3),
            'drop_pct': round(drop_pct, 2),
            'drop_from_open': round(s.get('drop_from_open', 0), 4),
            'drop_last_60': round(s.get('drop_last_60', 0), 4),
            'retrace_last_30': round(s.get('retrace_last_30', 0), 4),
            'snapshots': snapshots,
            'snapshots_last_60': s.get('snapshots_last_60', 0),
            'confidence': round(confidence, 3),
            'consensus_flag': s.get('consensus_flag', 1 if has_consensus else 0),
            'steam_flag': s.get('steam_flag', 0),
            'market_margin': round(s.get('market_margin', 0), 4),
            'score': round(score, 1),
            'type': f"T{tier}:{sig_type}" if tier else sig_type,
            'reason': reason,
        }

        candidates.append(row)

    if not candidates:
        return []

    # Sort by score descending
    candidates.sort(key=lambda r: float(r['score']), reverse=True)

    # Write to file (append-only, no duplicates)
    tsv_path = _novci_file()
    existing_keys = set()
    if tsv_path.exists():
        try:
            with open(tsv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    key = f"{row.get('kick_off', '')}|{row.get('match', '')}|{row.get('bet', '')}"
                    existing_keys.add(key)
        except Exception:
            pass

    file_exists = tsv_path.exists()
    new_rows = []

    with open(tsv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=NOVCI_COLUMNS, delimiter='\t',
                                extrasaction='ignore')
        if not file_exists:
            writer.writeheader()

        for row in candidates:
            key = f"{row['kick_off']}|{row['match']}|{row['bet']}"
            if key not in existing_keys:
                existing_keys.add(key)
                writer.writerow(row)
                new_rows.append(row)

    return new_rows


def format_novci_signal(row: dict) -> str:
    """One-line format for terminal display of a NOVCI signal."""
    cons = 'DA' if row.get('consensus_flag', 0) else 'NE'
    return (
        f"  ⭐ [{row.get('type', '')}] SCORE:{row.get('score', '?')} | "
        f"{row.get('kick_off', '')} ({row.get('min_to_ko', '?')}min) | "
        f"{row.get('match', '')} | {row.get('bet', '')} "
        f"@ {row.get('odds', '')} (drop {row.get('drop_pct', '')}) | "
        f"{row.get('snapshots', '?')} snaps | "
        f"consensus: {cons}"
    )


# ─── WEEKLY SIGNALS (early detection for future matches) ────────

def capture_week_signals(future_matches: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Capture early-detection signals for future matches (tomorrow, this week).
    Works DIRECTLY with raw match data — runs its own analysis via analyze_match().

    Two tiers:
      - confirmed: passes ALL NOVCI filters (conf >= 90%, 30+ snaps)
      - early:     relaxed thresholds  (conf >= 65%,  5+ snaps)

    DEDUP: one entry per match+bet (not per date!) — keeps the correct
    match_date (smallest days_until = actual scheduled date).

    Args:
        future_matches: list of match dicts with '_match_date' metadata

    Returns list of newly captured weekly-signal rows.
    """
    from odds_tracker.analyzer import analyze_match

    now = datetime.now()

    # ── 1. Build candidates, dedup in-memory by match+bet ──
    #    Keep only the entry with smallest days_until per match+bet.
    best: dict[str, dict] = {}          # key = "match|bet" → row
    analyzed_cache: dict[str, dict] = {} # key = "home|away" → analysis

    for m in future_matches:
        m_date = m.get('_match_date', '')
        if not m_date:
            continue

        try:
            match_dt = datetime.strptime(m_date, '%Y-%m-%d')
            days_until = (match_dt.date() - now.date()).days
        except ValueError:
            continue

        if days_until < 1:
            continue

        home = m.get('home', '')
        away = m.get('away', '')
        ko_str = m.get('kick_off', '')
        if not home or not away:
            continue

        # Cache analysis per team pair (same DB data regardless of date)
        cache_key = f"{home}|{away}"
        if cache_key not in analyzed_cache:
            try:
                analyzed_cache[cache_key] = analyze_match(home, away, ko_str)
            except Exception:
                analyzed_cache[cache_key] = None

        analysis = analyzed_cache[cache_key]
        if analysis is None:
            continue

        snapshots = analysis.get('snapshots', 0)

        # Minimum bar: at least WEEKLY_EARLY_MIN_SNAPSHOTS
        if snapshots < WEEKLY_EARLY_MIN_SNAPSHOTS:
            continue

        # Check each outcome (1, X, 2)
        for outcome in ['1', 'X', '2']:
            # Draws only blocked if NOVCI_EXCLUDE_DRAWS is True
            if NOVCI_EXCLUDE_DRAWS and outcome == 'X':
                continue

            pct_change = analysis.get(f'pct_change_{outcome.lower()}', 0)
            latest_odds = analysis['latest_odds'].get(outcome)
            opening_odds = analysis['opening_odds'].get(outcome)

            if not latest_odds or not opening_odds:
                continue

            # Max odds filter (applies to both tiers)
            if latest_odds > NOVCI_MAX_ODDS:
                continue

            # Min drop filter (applies to both tiers)
            if abs(pct_change) < NOVCI_MIN_DROP_PCT:
                continue

            # ── Build confidence score ──
            confidence = 0.0
            reasons = []
            sig_type = ''

            if analysis.get('late_sharp') == outcome:
                confidence += 0.35
                reasons.append(f'Late sharp move on {outcome}')
                sig_type = 'LATE_SHARP'

            if analysis.get('steam_move') == outcome:
                confidence += 0.30
                reasons.append(f'Steam move: {pct_change:+.1%}')
                if not sig_type:
                    sig_type = 'STEAM'

            if pct_change <= -0.05:
                confidence += 0.20
                reasons.append(f'Drop: {opening_odds:.2f} \u2192 {latest_odds:.2f} ({pct_change:+.1%})')
                if not sig_type:
                    sig_type = 'SUSTAINED_DROP'

            if pct_change <= -0.08:
                confidence += 0.15
                reasons.append('Strong drop >8%')

            other_outcomes = [o for o in ['1', 'X', '2'] if o != outcome]
            others_rising = all(
                analysis.get(f'pct_change_{o.lower()}', 0) > 0.01
                for o in other_outcomes
            )
            has_consensus = False
            if others_rising and pct_change < -0.02:
                confidence += 0.15
                reasons.append('Market consensus \u2014 others rising')
                has_consensus = True

            if snapshots >= 5:
                confidence += 0.05
                reasons.append(f'{snapshots} snapshots')

            confidence = min(confidence, 0.95)

            # ── Signal type check (must be STEAM or LATE_SHARP) ──
            if sig_type not in NOVCI_ALLOWED_TYPES:
                continue

            # ── Determine tier / stage ──
            if confidence >= NOVCI_MIN_CONFIDENCE and snapshots >= NOVCI_MIN_SNAPSHOTS:
                stage = 'confirmed'
            elif confidence >= WEEKLY_EARLY_MIN_CONFIDENCE and snapshots >= WEEKLY_EARLY_MIN_SNAPSHOTS:
                stage = 'early'
                reasons.append('EARLY')
            else:
                continue   # does not meet even relaxed thresholds

            score = _calculate_novci_score(pct_change, confidence, snapshots, has_consensus)

            bet_label = {'1': f'1 ({home})', 'X': 'X (Draw)', '2': f'2 ({away})'}.get(outcome, outcome)
            match_str = f'{home} vs {away}'

            # Compute structured fields
            import hashlib as _hl
            _raw = f"{m.get('country', '')}|{m.get('league', '')}|{m_date}|{ko_str}|{home}|{away}"
            w_match_id = _hl.md5(_raw.encode()).hexdigest()[:12]
            try:
                w_kickoff_utc = datetime.strptime(f"{m_date} {ko_str}", '%Y-%m-%d %H:%M').isoformat()
            except ValueError:
                w_kickoff_utc = ''
            w_drop_from_open = ((latest_odds - opening_odds) / opening_odds
                                if opening_odds > 1.0 else 0.0)
            w_steam_flag = 1 if analysis.get('steam_move') == outcome else 0
            # market margin
            _o1 = analysis['latest_odds'].get('1') or 0
            _ox = analysis['latest_odds'].get('X') or 0
            _o2 = analysis['latest_odds'].get('2') or 0
            w_margin = round((1/_o1 + 1/_ox + 1/_o2) - 1.0, 4) if (_o1 > 1 and _ox > 1 and _o2 > 1) else 0.0

            # Dedup key: match + bet only (NOT per date)
            dedup_key = f"{match_str}|{bet_label}"

            row = {
                'captured_at': now.strftime('%H:%M:%S'),
                'match_id': w_match_id,
                'match_date': m_date,
                'days_until': days_until,
                'kick_off': ko_str,
                'kickoff_utc': w_kickoff_utc,
                'match': match_str,
                'bet': bet_label,
                'odds': round(latest_odds, 3),
                'opening': round(opening_odds, 3),
                'drop_pct': round(pct_change, 4),
                'drop_from_open': round(w_drop_from_open, 4),
                'snapshots': snapshots,
                'snapshots_last_60': 0,
                'confidence': round(confidence, 3),
                'consensus_flag': 1 if has_consensus else 0,
                'steam_flag': w_steam_flag,
                'market_margin': w_margin,
                'score': round(score, 1),
                'type': sig_type,
                'stage': stage,
                'reason': ' | '.join(reasons),
            }

            # Keep only the entry with the smallest days_until (real match date)
            if dedup_key not in best or days_until < int(best[dedup_key].get('days_until', 9999)):
                best[dedup_key] = row

    if not best:
        return [], []

    candidates = sorted(best.values(), key=lambda r: float(r['score']), reverse=True)

    # ── 2. Write to file — OVERWRITE (not append) for clean dedup ──
    #    Weekly signals are regenerated every cycle; old stale entries
    #    get replaced with fresh data.
    tsv_path = _weekly_file()

    # Load existing entries (from previous cycles today) — merge with new
    existing: dict[str, dict] = {}
    if tsv_path.exists():
        try:
            with open(tsv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    key = f"{row.get('match', '')}|{row.get('bet', '')}"
                    existing[key] = row
        except Exception:
            pass

    # Merge: new data wins (fresher analysis)
    # Track NEW signals and CHANGED signals (odds moved, stage upgraded)
    new_rows = []      # completely new entries
    changed_rows = []   # existing entries where odds/score/stage changed
    for row in candidates:
        key = f"{row['match']}|{row['bet']}"
        if key not in existing:
            new_rows.append(row)
        else:
            # Check if anything meaningful changed vs previous version
            old = existing[key]
            old_odds = old.get('odds', '')
            new_odds = row.get('odds', '')
            old_stage = old.get('stage', 'early')
            new_stage = row.get('stage', 'early')
            old_score = old.get('score', '0')
            new_score = row.get('score', '0')
            old_drop = old.get('drop_pct', '')
            new_drop = row.get('drop_pct', '')
            if (old_odds != new_odds or old_stage != new_stage
                    or old_score != new_score or old_drop != new_drop):
                row['_prev_odds'] = old_odds
                row['_prev_drop'] = old_drop
                row['_prev_score'] = old_score
                changed_rows.append(row)
        existing[key] = row    # overwrite with fresh data

    # Rewrite entire file with merged data, sorted by score
    all_rows = sorted(existing.values(), key=lambda r: float(r.get('score', '0')), reverse=True)

    with open(tsv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=WEEKLY_COLUMNS, delimiter='\t',
                                extrasaction='ignore')
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    return new_rows, changed_rows


def format_weekly_signal(row: dict) -> str:
    """One-line format for terminal display of a weekly signal."""
    return (
        f"  \U0001f4c5 [{row.get('type', '')}] SCORE:{row.get('score', '?')} | "
        f"{row.get('match_date', '')} (za {row.get('days_until', '?')}d) {row.get('kick_off', '')} | "
        f"{row.get('match', '')} | {row.get('bet', '')} "
        f"@ {row.get('odds', '')} (drop {row.get('drop_pct', '')}) | "
        f"{row.get('snapshots', '?')} snaps | "
        f"consensus: {row.get('market_consensus', '?')}"
    )


def format_weekly_update(row: dict) -> str:
    """One-line format for terminal display of a CHANGED weekly signal."""
    prev_odds = row.get('_prev_odds', '?')
    prev_drop = row.get('_prev_drop', '?')
    prev_score = row.get('_prev_score', '?')
    return (
        f"  \U0001f4c8 [{row.get('type', '')}] SCORE:{prev_score}\u2192{row.get('score', '?')} | "
        f"{row.get('match_date', '')} (za {row.get('days_until', '?')}d) {row.get('kick_off', '')} | "
        f"{row.get('match', '')} | {row.get('bet', '')} "
        f"odds {prev_odds}\u2192{row.get('odds', '')} (drop {prev_drop}\u2192{row.get('drop_pct', '')}) | "
        f"{row.get('snapshots', '?')} snaps"
    )


# ─── DISPLAY ────────────────────────────────────────────────────

def format_golden_signal(row: dict) -> str:
    """One-line format for terminal display of a golden-window signal."""
    return (
        f"  >> [{row.get('type', '')}] {row.get('kick_off', '')} "
        f"({row.get('min_to_ko', '?')}min) | "
        f"{row.get('match', '')} | {row.get('bet', '')} "
        f"@ {row.get('odds', '')} | "
        f"{row.get('confidence', '')} confidence | {row.get('change', '')}"
    )


def print_signal_map():
    """Pretty-print current live signals, final bets, NOVCI, and weekly signals."""
    live_path = _live_file()
    final_path = _final_file()
    novci_path = _novci_file()
    weekly_path = _weekly_file()

    # Read live signals
    live_rows = []
    if live_path.exists():
        with open(live_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            live_rows = list(reader)

    # Read final bets
    final_rows = []
    if final_path.exists():
        with open(final_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            final_rows = list(reader)

    # Read NOVCI
    novci_rows = []
    if novci_path.exists():
        with open(novci_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            novci_rows = list(reader)

    print(f"\n  {'='*95}")
    print(f"   LIVE SIGNALS — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   File: {live_path.name}")
    print(f"  {'='*95}")

    if live_rows:
        print(f"  {'KO':>5}  {'Min':>3}  {'Type':<12}  {'Match':<35}  {'Bet':<20}  {'Odds':>6}  {'Conf':>5}  {'Move':>7}")
        print(f"  {'-'*95}")
        for r in live_rows[:30]:
            print(
                f"  {r.get('kick_off', ''):>5}  "
                f"{r.get('min_to_ko', ''):>3}  "
                f"{r.get('type', ''):<12}  "
                f"{r.get('match', '')[:35]:<35}  "
                f"{r.get('bet', '')[:20]:<20}  "
                f"{r.get('odds', ''):>6}  "
                f"{r.get('confidence', ''):>5}  "
                f"{r.get('change', ''):>7}"
            )
        if len(live_rows) > 30:
            print(f"  ... and {len(live_rows) - 30} more (see TSV file)")
        print(f"  {'-'*95}")
        print(f"  Total: {len(live_rows)} live signals")
    else:
        print("  No live signals yet.")

    # ── NOVCI ──
    print(f"\n  {'='*95}")
    print(f"   NOVCI — Best Bets (golden rules)")
    print(f"   File: {novci_path.name}")
    print(f"  {'='*95}")

    if novci_rows:
        print(f"  {'Uhvacen':>8}  {'KO':>5}  {'Score':>5}  {'Match':<35}  {'Bet':<20}  {'Odds':>5}  {'Drop':>7}  {'Snaps':>5}  {'Cons':>3}")
        print(f"  {'-'*95}")
        for r in novci_rows[-20:]:
            print(
                f"  {r.get('captured_at', ''):>8}  "
                f"{r.get('kick_off', ''):>5}  "
                f"{r.get('score', ''):>5}  "
                f"{r.get('match', '')[:35]:<35}  "
                f"{r.get('bet', '')[:20]:<20}  "
                f"{r.get('odds', ''):>5}  "
                f"{r.get('drop_pct', ''):>7}  "
                f"{r.get('snapshots', ''):>5}  "
                f"{r.get('consensus_flag', ''):>3}"
            )
        print(f"  {'-'*95}")
        print(f"  Total: {len(novci_rows)} NOVCI bets")
    else:
        print("  No NOVCI bets yet.")

    # ── WEEKLY SIGNALS ──
    weekly_rows = []
    if weekly_path.exists():
        with open(weekly_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            weekly_rows = list(reader)

    if weekly_rows:
        print(f"\n  {'='*95}")
        print(f"   WEEKLY SIGNALS — Future matches (early detection)")
        print(f"   File: {weekly_path.name}")
        print(f"  {'='*95}")
        print(f"  {'Datum':>10}  {'Za':>3}d  {'KO':>5}  {'Score':>5}  {'Match':<35}  {'Bet':<20}  {'Odds':>5}  {'Drop':>7}  {'Cons':>3}")
        print(f"  {'-'*95}")
        for r in weekly_rows[-20:]:
            print(
                f"  {r.get('match_date', ''):>10}  "
                f"{r.get('days_until', ''):>3}d  "
                f"{r.get('kick_off', ''):>5}  "
                f"{r.get('score', ''):>5}  "
                f"{r.get('match', '')[:35]:<35}  "
                f"{r.get('bet', '')[:20]:<20}  "
                f"{r.get('odds', ''):>5}  "
                f"{r.get('drop_pct', ''):>7}  "
                f"{r.get('consensus_flag', ''):>3}"
            )
        print(f"  {'-'*95}")
        print(f"  Total: {len(weekly_rows)} weekly signals")

    print(f"\n  {'-'*95}")
    print(f"   FINAL BETS (5-9 min window): {len(final_rows)} captured")
    print(f"   File: {final_path.name}")
    print(f"  {'-'*95}")

    if final_rows:
        for r in final_rows[-10:]:
            print(
                f"  {r.get('captured_at', ''):>8}  "
                f"{r.get('kick_off', ''):>5}  "
                f"{r.get('match', '')[:35]:<35}  "
                f"{r.get('bet', '')[:20]:<20}  "
                f"@ {r.get('odds', ''):>5}  "
                f"{r.get('confidence', ''):>5}"
            )
