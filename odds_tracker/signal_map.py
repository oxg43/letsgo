"""
Signal Map — Triple-file signal tracking system.

Three files are maintained:

1. LIVE_SIGNALS_<date>.tsv  — Rewritten EVERY cycle with ALL current signals.
   Always up to date. Open this file for the latest state.

2. FINAL_BETS_<date>.tsv    — Append-only. Signals captured 5-9 min before KO.
   This is the "golden window" file for actual betting decisions.

3. ZLATNA_PRAVILA_<date>.tsv — Append-only. BEST signals only (9-5 min before KO).
   Filtered by golden rules learned from today's data analysis:
   - Only STEAM or LATE_SHARP (no MIXED/SUSTAINED_DROP alone)
   - Confidence >= 80%
   - Odds drop >= 8%
   - Market consensus (other outcomes rising)
   - 30+ snapshots for robust data
   - Score-ranked by strength
"""
import csv
from datetime import datetime
from pathlib import Path

from odds_tracker.config import DATA_DIR

SIGNAL_MAP_DIR = DATA_DIR / "signal_map"
SIGNAL_MAP_DIR.mkdir(exist_ok=True)

# Golden window: 5-9 minutes before kick-off (user requirement)
GOLDEN_MIN = 5
GOLDEN_MAX = 9

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

FINAL_COLUMNS = ['captured_at'] + TSV_COLUMNS

# ─── ZLATNA PRAVILA columns ────────────────────────────────────
ZLATNA_COLUMNS = [
    'captured_at',
    'kick_off',
    'min_to_ko',
    'match',
    'bet',
    'odds',
    'opening',
    'drop_pct',
    'snapshots',
    'confidence',
    'score',
    'market_consensus',
    'type',
    'reason',
]

# ─── ZLATNA PRAVILA RULES (learned from today's analysis) ──────
# These parameters were derived from analyzing 74,500+ snapshots,
# 5,129 signals, and 116 final bets on 2026-02-07.
#
# What works (positive ROI):
#   - STEAM moves with >8% drop in T3-4 leagues
#   - LATE_SHARP with market consensus, 95% confidence
#   - Home favorites, minutes 20-65 in lower leagues
#   - 40+ snapshots = robust data, high reliability
#
# What doesn't work:
#   - MIXED signals (only 30% confidence) → random noise
#   - SUSTAINED_DROP alone without steam → often reversal
#   - Draws at high odds (>5.0) → Poisson overestimates
#   - Odds drop <5% → within normal noise range
#
ZLATNA_MIN_CONFIDENCE = 0.80     # skip weak 30%/50%/65% signals
ZLATNA_MIN_DROP_PCT = 0.08       # at least 8% odds drop
ZLATNA_MIN_SNAPSHOTS = 30        # need robust data
ZLATNA_ALLOWED_TYPES = {'STEAM', 'LATE_SHARP'}  # only proven types
ZLATNA_MAX_DRAW_ODDS = 5.0       # skip extreme draw odds


def _live_file() -> Path:
    """Current live signals file (overwritten every cycle)."""
    return SIGNAL_MAP_DIR / f"LIVE_SIGNALS_{datetime.now().strftime('%Y-%m-%d')}.tsv"


def _final_file() -> Path:
    """Golden window bets file (append-only)."""
    return SIGNAL_MAP_DIR / f"FINAL_BETS_{datetime.now().strftime('%Y-%m-%d')}.tsv"


def _zlatna_file() -> Path:
    """Zlatna Pravila file — best golden-rule bets (append-only)."""
    return SIGNAL_MAP_DIR / f"ZLATNA_PRAVILA_{datetime.now().strftime('%Y-%m-%d')}.tsv"


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
    today_str = now.strftime('%Y-%m-%d')
    ko_str = s.get('kick_off', '')
    if not ko_str:
        return None

    try:
        ko_dt = datetime.strptime(f"{today_str} {ko_str}", '%Y-%m-%d %H:%M')
        minutes_to_ko = (ko_dt - now).total_seconds() / 60
    except ValueError:
        return None

    # Skip matches that already started (more than 5 min ago)
    if minutes_to_ko < -5:
        return None

    return {
        'kick_off': ko_str,
        'min_to_ko': f"{max(0, minutes_to_ko):.0f}",
        'type': s.get('signal_type', 'SIGNAL'),
        'match': f"{s.get('home', '?')} vs {s.get('away', '?')}",
        'bet': _bet_label(s),
        'odds': f"{s.get('latest_odds', 0):.2f}",
        'confidence': f"{s.get('confidence', 0):.0%}",
        'change': f"{s.get('pct_change', 0):+.1%}",
        'opening': f"{s.get('opening_odds', 0):.2f}",
        'edge': '',
        'fair_odds': '',
        'kelly': '',
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
    rows.sort(key=lambda r: (r['kick_off'], -float(r['confidence'].rstrip('%')) / 100))

    # Overwrite the file completely
    with open(tsv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=TSV_COLUMNS, delimiter='\t')
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
        try:
            ko_dt = datetime.strptime(f"{today_str} {ko_str}", '%Y-%m-%d %H:%M')
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
        writer = csv.DictWriter(f, fieldnames=FINAL_COLUMNS, delimiter='\t')
        if not file_exists:
            writer.writeheader()

        for row in golden:
            key = f"{row['kick_off']}|{row['match']}|{row['bet']}"
            if key not in existing_keys:
                existing_keys.add(key)
                writer.writerow(row)
                new_rows.append(row)

    return new_rows


# ─── ZLATNA PRAVILA (append-only, golden rules 9-5 min) ────────

def _calculate_zlatna_score(pct_drop: float, confidence: float, snapshots: int,
                            has_consensus: bool) -> float:
    """
    Calculate quality score for a golden-rule signal.
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


def capture_zlatna_pravila(signals: list[dict], value_bets: list[dict] = None) -> list[dict]:
    """
    Capture ONLY the highest-quality signals in the golden window (9-5 min
    before kick-off) using strict golden rules learned from today's data.

    Rules:
    1. Only STEAM or LATE_SHARP signal types
    2. Confidence >= 80%
    3. Odds drop >= 8% (absolute)
    4. At least 30 snapshots
    5. No extreme draw bets (odds > 5.0)
    6. Score-ranked by composite quality metric

    Returns list of newly captured golden-rule rows.
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
        try:
            ko_dt = datetime.strptime(f"{today_str} {ko_str}", '%Y-%m-%d %H:%M')
            minutes_to_ko = (ko_dt - now).total_seconds() / 60
        except ValueError:
            continue

        # Must be in golden window: 5-9 minutes before kickoff
        if not (GOLDEN_MIN <= minutes_to_ko <= GOLDEN_MAX):
            continue

        # ── ZLATNA PRAVILA FILTERS ──

        # Rule 1: Only STEAM or LATE_SHARP
        sig_type = s.get('signal_type', '')
        if sig_type not in ZLATNA_ALLOWED_TYPES:
            continue

        # Rule 2: Confidence >= 80%
        confidence = s.get('confidence', 0)
        if confidence < ZLATNA_MIN_CONFIDENCE:
            continue

        # Rule 3: Odds drop >= 8%
        pct_change = s.get('pct_change', 0)
        if abs(pct_change) < ZLATNA_MIN_DROP_PCT:
            continue

        # Rule 4: At least 30 snapshots
        snapshots = s.get('snapshots', 0)
        if snapshots < ZLATNA_MIN_SNAPSHOTS:
            continue

        # Rule 5: No extreme draw bets
        bet = s.get('bet', '')
        latest_odds = s.get('latest_odds', 0)
        if bet == 'X' and latest_odds > ZLATNA_MAX_DRAW_ODDS:
            continue

        # ── PASSED ALL FILTERS — calculate score ──
        reason = s.get('reason', '')
        has_consensus = 'market consensus' in reason.lower() or 'Other outcomes rising' in reason

        score = _calculate_zlatna_score(pct_change, confidence, snapshots, has_consensus)

        row = {
            'captured_at': now.strftime('%H:%M:%S'),
            'kick_off': ko_str,
            'min_to_ko': f"{minutes_to_ko:.0f}",
            'match': f"{s.get('home', '?')} vs {s.get('away', '?')}",
            'bet': _bet_label(s),
            'odds': f"{latest_odds:.2f}",
            'opening': f"{s.get('opening_odds', 0):.2f}",
            'drop_pct': f"{pct_change:+.1%}",
            'snapshots': str(snapshots),
            'confidence': f"{confidence:.0%}",
            'score': f"{score:.1f}",
            'market_consensus': 'DA' if has_consensus else 'NE',
            'type': sig_type,
            'reason': reason,
        }

        candidates.append(row)

    if not candidates:
        return []

    # Sort by score descending
    candidates.sort(key=lambda r: float(r['score']), reverse=True)

    # Write to file (append-only, no duplicates)
    tsv_path = _zlatna_file()
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
        writer = csv.DictWriter(f, fieldnames=ZLATNA_COLUMNS, delimiter='\t')
        if not file_exists:
            writer.writeheader()

        for row in candidates:
            key = f"{row['kick_off']}|{row['match']}|{row['bet']}"
            if key not in existing_keys:
                existing_keys.add(key)
                writer.writerow(row)
                new_rows.append(row)

    return new_rows


def format_zlatna_signal(row: dict) -> str:
    """One-line format for terminal display of a zlatna pravila signal."""
    return (
        f"  ⭐ [{row.get('type', '')}] SCORE:{row.get('score', '?')} | "
        f"{row.get('kick_off', '')} ({row.get('min_to_ko', '?')}min) | "
        f"{row.get('match', '')} | {row.get('bet', '')} "
        f"@ {row.get('odds', '')} (drop {row.get('drop_pct', '')}) | "
        f"{row.get('snapshots', '?')} snaps | "
        f"consensus: {row.get('market_consensus', '?')}"
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
    """Pretty-print current live signals, final bets, and zlatna pravila summary."""
    live_path = _live_file()
    final_path = _final_file()
    zlatna_path = _zlatna_file()

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

    # Read zlatna pravila
    zlatna_rows = []
    if zlatna_path.exists():
        with open(zlatna_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            zlatna_rows = list(reader)

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

    # ── ZLATNA PRAVILA ──
    print(f"\n  {'='*95}")
    print(f"   ZLATNA PRAVILA — Best Bets (golden rules)")
    print(f"   File: {zlatna_path.name}")
    print(f"  {'='*95}")

    if zlatna_rows:
        print(f"  {'Uhvacen':>8}  {'KO':>5}  {'Score':>5}  {'Match':<35}  {'Bet':<20}  {'Odds':>5}  {'Drop':>7}  {'Snaps':>5}  {'Cons':>3}")
        print(f"  {'-'*95}")
        for r in zlatna_rows[-20:]:
            print(
                f"  {r.get('captured_at', ''):>8}  "
                f"{r.get('kick_off', ''):>5}  "
                f"{r.get('score', ''):>5}  "
                f"{r.get('match', '')[:35]:<35}  "
                f"{r.get('bet', '')[:20]:<20}  "
                f"{r.get('odds', ''):>5}  "
                f"{r.get('drop_pct', ''):>7}  "
                f"{r.get('snapshots', ''):>5}  "
                f"{r.get('market_consensus', ''):>3}"
            )
        print(f"  {'-'*95}")
        print(f"  Total: {len(zlatna_rows)} zlatna pravila bets")
    else:
        print("  No zlatna pravila bets yet.")

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
