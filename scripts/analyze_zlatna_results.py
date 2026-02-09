"""
Comprehensive analysis of ZLATNA PRAVILA & FINAL_BETS signal outcomes.

Cross-references signals from all dates with movement_log.csv final scores
to determine win/loss/push for every pre-match signal.

Produces:
  - Overall hit rate & ROI by signal type (STEAM, LATE_SHARP, MIXED, etc.)
  - Hit rate by confidence bucket, drop_pct bucket, odds range, snapshots
  - Market consensus (DA vs NE) comparison
  - Bet type analysis (1 vs X vs 2)
  - Score threshold optimization
  - Combined filter optimization grid
  - LIVE_VALUE signal performance (separate analysis)
  - Suggested parameter tweaks for signal_map.py ZLATNA rules
"""

import csv
import re
import os
import sys
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

# ─── CONFIG ─────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
SIGNAL_MAP_DIR = BASE_DIR / "odds_data" / "signal_map"
MOVEMENT_LOG = BASE_DIR / "odds_data" / "movement" / "movement_log.csv"

# ─── HELPERS ────────────────────────────────────────────────────

def parse_score(score_str: str) -> tuple:
    """Parse 'X-Y' score into (home_goals, away_goals) or None."""
    if not score_str or score_str.strip() == '':
        return None
    m = re.match(r'(\d+)\s*[-:]\s*(\d+)', score_str.strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def parse_bet(bet_str: str) -> str:
    """Extract bet outcome: '1', 'X', or '2' from bet label like '1 (TeamName)', 'X (Draw)', '2 (TeamName)'."""
    bet_str = bet_str.strip()
    if bet_str.startswith('1'):
        return '1'
    elif bet_str.startswith('X') or bet_str.startswith('x'):
        return 'X'
    elif bet_str.startswith('2'):
        return '2'
    return bet_str


def determine_result(bet: str, home_goals: int, away_goals: int) -> str:
    """Determine if bet WON, LOST, or PUSHED."""
    if bet == '1':
        if home_goals > away_goals:
            return 'WON'
        elif home_goals == away_goals:
            return 'LOST'  # 1X2 has no push
        else:
            return 'LOST'
    elif bet == 'X':
        if home_goals == away_goals:
            return 'WON'
        else:
            return 'LOST'
    elif bet == '2':
        if away_goals > home_goals:
            return 'WON'
        elif home_goals == away_goals:
            return 'LOST'
        else:
            return 'LOST'
    return 'UNKNOWN'


def normalize_team(name: str) -> str:
    """Normalize team name for matching."""
    name = name.strip().lower()
    # Remove common suffixes
    for suffix in [' fc', ' sc', ' cf', ' ac', ' ec', ' se']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name.strip()


def parse_pct(pct_str: str) -> float:
    """Parse percentage string like '-10.3%' or '+18.0%' to float."""
    if not pct_str:
        return 0.0
    pct_str = pct_str.strip().replace('%', '').replace('+', '')
    try:
        return float(pct_str)
    except ValueError:
        return 0.0


def parse_conf(conf_str: str) -> float:
    """Parse confidence string like '95%' to float 0.95."""
    if not conf_str:
        return 0.0
    conf_str = conf_str.strip().replace('%', '')
    try:
        return float(conf_str) / 100.0
    except ValueError:
        return 0.0


# ─── LOAD FINAL SCORES FROM movement_log.csv ───────────────────

def load_final_scores() -> dict:
    """
    Build a lookup: (home_normalized, away_normalized, kick_off) -> score
    Uses the LAST entry per match from movement_log (most recent scrape).
    """
    print(f"  Loading movement_log.csv ({MOVEMENT_LOG})...")
    scores = {}
    total = 0
    finished_count = 0

    with open(MOVEMENT_LOG, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            total += 1
            status = row.get('status', '').strip()
            if status != 'finished':
                continue
            finished_count += 1
            
            home = normalize_team(row.get('home', ''))
            away = normalize_team(row.get('away', ''))
            ko = row.get('kick_off', '').strip()
            score = row.get('score', '').strip()
            
            if home and away and score:
                key = (home, away, ko)
                scores[key] = score  # last entry wins (most up-to-date)

    print(f"  Loaded {total:,} rows, {finished_count:,} finished, {len(scores):,} unique matches with scores")
    return scores


def find_score(scores: dict, match_str: str, kick_off: str) -> str | None:
    """
    Try to match 'Home vs Away' string against the scores lookup.
    Returns score string or None.
    """
    if ' vs ' not in match_str:
        return None
    
    parts = match_str.split(' vs ', 1)
    home = normalize_team(parts[0])
    away = normalize_team(parts[1])
    
    # Try exact match
    key = (home, away, kick_off)
    if key in scores:
        return scores[key]
    
    # Try without kick_off time (fuzzy)
    for (h, a, ko), score in scores.items():
        if h == home and a == away:
            return score
        # Try partial home match
        if (home in h or h in home) and (away in a or a in away):
            return score
    
    return None


# ─── LOAD SIGNALS ──────────────────────────────────────────────

def load_all_signals(file_pattern: str, is_zlatna: bool = False) -> list:
    """Load all signal TSV files matching pattern."""
    signals = []
    
    for tsv_path in sorted(SIGNAL_MAP_DIR.glob(file_pattern)):
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', tsv_path.name)
        date_str = date_match.group(1) if date_match else 'unknown'
        
        try:
            with open(tsv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    row['_file_date'] = date_str
                    row['_source'] = tsv_path.name
                    signals.append(row)
        except Exception as e:
            print(f"  Warning: Error reading {tsv_path.name}: {e}")
    
    return signals


# ─── MAIN ANALYSIS ─────────────────────────────────────────────

def run_analysis():
    print("\n" + "=" * 100)
    print("  ZLATNA PRAVILA — COMPREHENSIVE SIGNAL ANALYSIS")
    print("  Cross-referencing signals with final match results")
    print("=" * 100)

    # 1. Load final scores
    scores = load_final_scores()

    # 2. Load FINAL_BETS (broader set — includes MIXED, SUSTAINED_DROP)
    final_signals = load_all_signals("FINAL_BETS_*.tsv")
    print(f"\n  Loaded {len(final_signals)} FINAL_BETS signals across all dates")

    # 3. Load ZLATNA_PRAVILA (filtered set)
    zlatna_signals = load_all_signals("ZLATNA_PRAVILA_*.tsv", is_zlatna=True)
    print(f"  Loaded {len(zlatna_signals)} ZLATNA_PRAVILA signals across all dates")

    # ── Separate pre-match vs LIVE signals ──
    prematch_final = []
    live_final = []
    for s in final_signals:
        if s.get('min_to_ko', '').strip() == 'LIVE' or 'LIVE' in s.get('kick_off', ''):
            live_final.append(s)
        else:
            prematch_final.append(s)

    prematch_zlatna = []
    live_zlatna = []
    for s in zlatna_signals:
        ko = s.get('kick_off', '').strip()
        mtk = s.get('min_to_ko', '').strip()
        if mtk == 'LIVE' or 'LIVE' in ko:
            live_zlatna.append(s)
        else:
            prematch_zlatna.append(s)

    print(f"\n  Pre-match FINAL_BETS: {len(prematch_final)}")
    print(f"  Live FINAL_BETS: {len(live_final)}")
    print(f"  Pre-match ZLATNA: {len(prematch_zlatna)}")
    print(f"  Live ZLATNA (LIVE_VALUE): {len(live_zlatna)}")

    # ── Match signals to results ──
    def resolve_signals(signals_list, label):
        resolved = []
        matched = 0
        unmatched = 0
        
        for s in signals_list:
            match_str = s.get('match', '')
            kick_off = s.get('kick_off', '').strip()
            bet_label = s.get('bet', '')
            
            bet = parse_bet(bet_label)
            
            # Try to find score
            final_score = find_score(scores, match_str, kick_off)
            
            if final_score:
                parsed = parse_score(final_score)
                if parsed:
                    hg, ag = parsed
                    result = determine_result(bet, hg, ag)
                    
                    # Parse odds
                    try:
                        odds = float(s.get('odds', '0'))
                    except:
                        odds = 0.0
                    try:
                        opening = float(s.get('opening', '0'))
                    except:
                        opening = 0.0
                    
                    # Parse change/drop
                    change_str = s.get('change', s.get('drop_pct', ''))
                    drop_pct = parse_pct(change_str)
                    
                    # Parse confidence 
                    conf = parse_conf(s.get('confidence', ''))
                    
                    # Parse snapshots
                    snap_str = s.get('snapshots', '0')
                    try:
                        snaps = int(snap_str)
                    except:
                        snaps = 0
                    
                    # Parse score value
                    try:
                        score_val = float(s.get('score', '0'))
                    except:
                        score_val = 0.0
                    
                    # Market consensus
                    consensus = s.get('market_consensus', '')
                    
                    sig_type = s.get('type', s.get('signal_type', 'UNKNOWN'))
                    
                    reason = s.get('reason', '')
                    has_consensus_in_reason = 'market consensus' in reason.lower() or 'Other outcomes rising' in reason
                    
                    resolved.append({
                        'match': match_str,
                        'kick_off': kick_off,
                        'bet': bet,
                        'bet_label': bet_label,
                        'odds': odds,
                        'opening': opening,
                        'drop_pct': drop_pct,
                        'confidence': conf,
                        'snapshots': snaps,
                        'score_val': score_val,
                        'consensus': consensus if consensus else ('DA' if has_consensus_in_reason else 'NE'),
                        'type': sig_type,
                        'result': result,
                        'final_score': final_score,
                        'home_goals': hg,
                        'away_goals': ag,
                        'date': s.get('_file_date', ''),
                        'reason': reason,
                    })
                    matched += 1
                else:
                    unmatched += 1
            else:
                unmatched += 1
        
        print(f"\n  {label}: {matched} matched, {unmatched} unmatched")
        return resolved

    resolved_final = resolve_signals(prematch_final, "FINAL_BETS (pre-match)")
    resolved_zlatna = resolve_signals(prematch_zlatna, "ZLATNA_PRAVILA (pre-match)")
    resolved_live = resolve_signals(live_zlatna, "ZLATNA/LIVE_VALUE")

    # ================================================================
    # ANALYSIS FUNCTIONS
    # ================================================================

    def calc_stats(bets: list, min_count=1) -> dict:
        """Calculate hit rate and ROI for a list of bets."""
        if len(bets) < min_count:
            return {'count': len(bets), 'wins': 0, 'hit_rate': 0, 'roi': 0, 'profit': 0, 'avg_odds': 0}
        
        wins = sum(1 for b in bets if b['result'] == 'WON')
        count = len(bets)
        hit_rate = wins / count if count > 0 else 0
        
        # ROI: flat 1-unit stakes
        profit = sum(b['odds'] - 1 if b['result'] == 'WON' else -1 for b in bets)
        roi = profit / count if count > 0 else 0
        avg_odds = sum(b['odds'] for b in bets) / count if count > 0 else 0
        
        return {
            'count': count,
            'wins': wins,
            'hit_rate': hit_rate,
            'roi': roi,
            'profit': profit,
            'avg_odds': avg_odds,
        }

    def print_stats(label: str, stats: dict, indent=4):
        """Print formatted stats."""
        pad = ' ' * indent
        if stats['count'] == 0:
            print(f"{pad}{label}: No data")
            return
        emoji = "✅" if stats['roi'] > 0 else "❌"
        print(f"{pad}{emoji} {label}: {stats['count']} bets | "
              f"Hit: {stats['wins']}/{stats['count']} ({stats['hit_rate']:.1%}) | "
              f"ROI: {stats['roi']:+.1%} | "
              f"Profit: {stats['profit']:+.1f}u | "
              f"Avg odds: {stats['avg_odds']:.2f}")

    def analyze_dimension(bets, key_fn, label, indent=4):
        """Analyze bets grouped by a dimension."""
        groups = defaultdict(list)
        for b in bets:
            k = key_fn(b)
            groups[k].append(b)
        
        print(f"\n{'=' * 90}")
        print(f"  {label}")
        print(f"{'=' * 90}")
        
        sorted_keys = sorted(groups.keys(), key=lambda k: calc_stats(groups[k])['roi'], reverse=True)
        for k in sorted_keys:
            stats = calc_stats(groups[k])
            print_stats(str(k), stats, indent)

    # ================================================================
    # RUN ALL ANALYSES
    # ================================================================

    # ── A. FINAL_BETS OVERALL ──
    print("\n\n" + "#" * 100)
    print("  SECTION A: ALL FINAL_BETS (pre-match) — OVERALL")
    print("#" * 100)
    all_stats = calc_stats(resolved_final)
    print_stats("ALL FINAL_BETS", all_stats)

    # ── A.1 By signal type ──
    analyze_dimension(resolved_final, lambda b: b['type'], "A.1: BY SIGNAL TYPE")

    # ── A.2 By confidence ──
    def conf_bucket(b):
        c = b['confidence']
        if c >= 0.90: return '90-95%'
        elif c >= 0.80: return '80-89%'
        elif c >= 0.65: return '65-79%'
        elif c >= 0.50: return '50-64%'
        else: return '<50%'
    analyze_dimension(resolved_final, conf_bucket, "A.2: BY CONFIDENCE BUCKET")

    # ── A.3 By drop percentage ──
    def drop_bucket(b):
        d = abs(b['drop_pct'])
        if d >= 20: return '≥20% drop'
        elif d >= 15: return '15-20% drop'
        elif d >= 10: return '10-15% drop'
        elif d >= 8: return '8-10% drop'
        elif d >= 5: return '5-8% drop'
        else: return '<5% drop'
    analyze_dimension(resolved_final, drop_bucket, "A.3: BY ODDS DROP PERCENTAGE")

    # ── A.4 By odds range ──
    def odds_bucket(b):
        o = b['odds']
        if o < 1.30: return '<1.30 (heavy fav)'
        elif o < 1.60: return '1.30-1.59'
        elif o < 2.00: return '1.60-1.99'
        elif o < 2.50: return '2.00-2.49'
        elif o < 3.50: return '2.50-3.49'
        elif o < 5.00: return '3.50-4.99'
        else: return '≥5.00 (longshot)'
    analyze_dimension(resolved_final, odds_bucket, "A.4: BY ODDS RANGE")

    # ── A.5 By bet type (1/X/2) ──
    analyze_dimension(resolved_final, lambda b: b['bet'], "A.5: BY BET TYPE (1/X/2)")

    # ── A.6 By market consensus ──
    analyze_dimension(resolved_final, lambda b: b['consensus'], "A.6: BY MARKET CONSENSUS (DA/NE)")

    # ── A.7 By snapshots ──
    def snap_bucket(b):
        s = b['snapshots']
        if s >= 150: return '150+ snaps'
        elif s >= 100: return '100-149 snaps'
        elif s >= 60: return '60-99 snaps'
        elif s >= 40: return '40-59 snaps'
        elif s >= 30: return '30-39 snaps'
        else: return '<30 snaps'
    analyze_dimension(resolved_final, snap_bucket, "A.7: BY SNAPSHOT COUNT")

    # ── A.8 By date ──
    analyze_dimension(resolved_final, lambda b: b['date'], "A.8: BY DATE")

    # ═══════════════════════════════════════════════════════════════
    # B. ZLATNA PRAVILA FILTERED — HOW DID THE GOLDEN RULES PERFORM?
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "#" * 100)
    print("  SECTION B: ZLATNA PRAVILA (filtered golden rules — pre-match only)")
    print("#" * 100)
    
    # Only pre-match STEAM/LATE_SHARP from ZLATNA
    zlatna_prematch_quality = [b for b in resolved_zlatna if b['type'] in ('STEAM', 'LATE_SHARP')]
    all_zlatna_stats = calc_stats(zlatna_prematch_quality)
    print_stats("ZLATNA PRE-MATCH (STEAM+LATE_SHARP)", all_zlatna_stats)

    analyze_dimension(zlatna_prematch_quality, lambda b: b['type'], "B.1: ZLATNA BY SIGNAL TYPE")
    analyze_dimension(zlatna_prematch_quality, lambda b: b['consensus'], "B.2: ZLATNA BY MARKET CONSENSUS")
    analyze_dimension(zlatna_prematch_quality, odds_bucket, "B.3: ZLATNA BY ODDS RANGE")
    analyze_dimension(zlatna_prematch_quality, drop_bucket, "B.4: ZLATNA BY DROP %")
    analyze_dimension(zlatna_prematch_quality, conf_bucket, "B.5: ZLATNA BY CONFIDENCE")
    analyze_dimension(zlatna_prematch_quality, lambda b: b['bet'], "B.6: ZLATNA BY BET TYPE (1/X/2)")

    # ── B.7 By score threshold ──
    def score_bucket(b):
        s = b['score_val']
        if s >= 85: return '85+ score'
        elif s >= 75: return '75-84 score'
        elif s >= 65: return '65-74 score'
        elif s >= 55: return '55-64 score'
        else: return '<55 score'
    analyze_dimension(zlatna_prematch_quality, score_bucket, "B.7: ZLATNA BY SCORE THRESHOLD")

    # ═══════════════════════════════════════════════════════════════
    # C. COMBINED FILTER OPTIMIZATION
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "#" * 100)
    print("  SECTION C: FILTER OPTIMIZATION — Testing different thresholds on FINAL_BETS")
    print("#" * 100)

    # Only STEAM + LATE_SHARP from FINAL_BETS
    steam_ls = [b for b in resolved_final if b['type'] in ('STEAM', 'LATE_SHARP')]
    
    print(f"\n  Base: STEAM+LATE_SHARP from FINAL_BETS = {len(steam_ls)} bets")
    print_stats("Base (STEAM+LATE_SHARP)", calc_stats(steam_ls))

    # Test confidence thresholds
    print(f"\n  {'─' * 80}")
    print(f"  C.1: Confidence threshold tests:")
    for min_conf in [0.50, 0.65, 0.80, 0.90, 0.95]:
        filtered = [b for b in steam_ls if b['confidence'] >= min_conf]
        stats = calc_stats(filtered)
        tag = " ← CURRENT" if min_conf == 0.80 else ""
        print_stats(f"Conf ≥ {min_conf:.0%}{tag}", stats)

    # Test drop thresholds
    print(f"\n  {'─' * 80}")
    print(f"  C.2: Drop % threshold tests:")
    for min_drop in [5, 8, 10, 12, 15, 20]:
        filtered = [b for b in steam_ls if abs(b['drop_pct']) >= min_drop]
        stats = calc_stats(filtered)
        tag = " ← CURRENT" if min_drop == 8 else ""
        print_stats(f"Drop ≥ {min_drop}%{tag}", stats)

    # Test snapshot thresholds (using ZLATNA data which has snapshots)
    print(f"\n  {'─' * 80}")
    print(f"  C.3: Snapshot threshold tests (using ZLATNA data):")
    for min_snaps in [20, 30, 40, 50, 60, 80, 100]:
        filtered = [b for b in zlatna_prematch_quality if b['snapshots'] >= min_snaps]
        stats = calc_stats(filtered)
        tag = " ← CURRENT" if min_snaps == 30 else ""
        print_stats(f"Snaps ≥ {min_snaps}{tag}", stats)

    # Test consensus requirement
    print(f"\n  {'─' * 80}")
    print(f"  C.4: Market consensus requirement:")
    consensus_only = [b for b in steam_ls if b['consensus'] == 'DA']
    no_consensus = [b for b in steam_ls if b['consensus'] == 'NE']
    print_stats("Only DA (consensus)", calc_stats(consensus_only))
    print_stats("Only NE (no consensus)", calc_stats(no_consensus))

    # Test combined filters (using FINAL_BETS — snapshot filter skipped when unavailable)
    print(f"\n  {'─' * 80}")
    print(f"  C.5: Combined filter optimization (STEAM+LATE_SHARP from FINAL_BETS):")
    
    best_combos = []
    
    for min_conf in [0.50, 0.65, 0.80, 0.90, 0.95]:
        for min_drop in [5, 8, 10, 12, 15, 20]:
            for require_consensus in [True, False]:
                for exclude_draws in [True, False]:
                    for max_odds in [3.0, 5.0, 50.0]:
                        filtered = [b for b in steam_ls 
                                    if b['confidence'] >= min_conf
                                    and abs(b['drop_pct']) >= min_drop
                                    and (not require_consensus or b['consensus'] == 'DA')
                                    and (not exclude_draws or b['bet'] != 'X')
                                    and b['odds'] <= max_odds]
                        stats = calc_stats(filtered, min_count=5)
                        if stats['count'] >= 5:
                            combo_label = (f"Conf≥{min_conf:.0%} Drop≥{min_drop}% "
                                           f"Cons={'DA' if require_consensus else 'ALL'} "
                                           f"Draws={'NO' if exclude_draws else 'YES'} "
                                           f"MaxOdds={max_odds:.0f}")
                            best_combos.append((combo_label, stats))

    # Sort by ROI
    best_combos.sort(key=lambda x: x[1]['roi'], reverse=True)
    
    print(f"\n  Top 20 filter combos by ROI (min 5 bets):")
    for label, stats in best_combos[:20]:
        print_stats(label, stats)

    print(f"\n  Bottom 5 filter combos by ROI:")
    for label, stats in best_combos[-5:]:
        print_stats(label, stats)
    
    # C.5b: Combined filters on ZLATNA data (has snapshots + score)
    print(f"\n  {'─' * 80}")
    print(f"  C.5b: Combined filter optimization (ZLATNA data — has snapshots & score):")
    
    zlatna_combos = []
    for min_conf in [0.80, 0.90, 0.95]:
        for min_drop in [8, 10, 12, 15]:
            for min_snaps in [30, 40, 50, 60, 80]:
                for require_consensus in [True, False]:
                    for exclude_draws in [True, False]:
                        for min_score in [0, 65, 75]:
                            filtered = [b for b in zlatna_prematch_quality
                                        if b['confidence'] >= min_conf
                                        and abs(b['drop_pct']) >= min_drop
                                        and b['snapshots'] >= min_snaps
                                        and (not require_consensus or b['consensus'] == 'DA')
                                        and (not exclude_draws or b['bet'] != 'X')
                                        and b['score_val'] >= min_score]
                            stats = calc_stats(filtered, min_count=3)
                            if stats['count'] >= 3:
                                combo_label = (f"Conf≥{min_conf:.0%} Drop≥{min_drop}% Snaps≥{min_snaps} "
                                               f"Cons={'DA' if require_consensus else 'ALL'} "
                                               f"Draws={'NO' if exclude_draws else 'YES'} "
                                               f"Score≥{min_score}")
                                zlatna_combos.append((combo_label, stats))

    zlatna_combos.sort(key=lambda x: x[1]['roi'], reverse=True)
    print(f"\n  Top 20 ZLATNA combos by ROI (min 3 bets):")
    for label, stats in zlatna_combos[:20]:
        print_stats(label, stats)

    # ── C.6: Odds range filtering ──
    print(f"\n  {'─' * 80}")
    print(f"  C.6: Odds range filtering (on STEAM+LATE_SHARP conf≥80% drop≥8%):")
    base_filtered = [b for b in steam_ls if b['confidence'] >= 0.80 and abs(b['drop_pct']) >= 8]
    
    for max_odds in [2.0, 2.5, 3.0, 3.5, 5.0, 10.0, 50.0]:
        filtered = [b for b in base_filtered if b['odds'] <= max_odds]
        stats = calc_stats(filtered)
        print_stats(f"Odds ≤ {max_odds:.1f}", stats)

    for min_odds in [1.0, 1.3, 1.5, 2.0, 2.5, 3.0]:
        filtered = [b for b in base_filtered if b['odds'] >= min_odds]
        stats = calc_stats(filtered)
        print_stats(f"Odds ≥ {min_odds:.1f}", stats)

    # ── C.7: Draw (X) filter test ──
    print(f"\n  {'─' * 80}")
    print(f"  C.7: Draw (X) filtering tests:")
    draws = [b for b in base_filtered if b['bet'] == 'X']
    non_draws = [b for b in base_filtered if b['bet'] != 'X']
    print_stats("Only draws (X)", calc_stats(draws))
    print_stats("No draws (1 or 2 only)", calc_stats(non_draws))
    
    for max_draw_odds in [3.0, 3.5, 4.0, 5.0]:
        filtered = [b for b in base_filtered if not (b['bet'] == 'X' and b['odds'] > max_draw_odds)]
        stats = calc_stats(filtered)
        tag = " ← CURRENT" if max_draw_odds == 5.0 else ""
        print_stats(f"Exclude draws > {max_draw_odds:.1f}{tag}", stats)

    # ═══════════════════════════════════════════════════════════════
    # D. LIVE_VALUE SIGNAL ANALYSIS (from ZLATNA_PRAVILA)
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "#" * 100)
    print("  SECTION D: LIVE_VALUE SIGNALS (in-play)")
    print("#" * 100)

    if resolved_live:
        print_stats("ALL LIVE_VALUE", calc_stats(resolved_live))
        
        # By edge bucket
        def edge_bucket(b):
            d = abs(b['drop_pct'])  # drop_pct holds +edge for live
            if d >= 18: return '≥18% edge'
            elif d >= 15: return '15-18% edge'
            elif d >= 12: return '12-15% edge'
            else: return '<12% edge'
        analyze_dimension(resolved_live, edge_bucket, "D.1: LIVE_VALUE BY EDGE")
        analyze_dimension(resolved_live, lambda b: b['bet'], "D.2: LIVE_VALUE BY BET TYPE")
        analyze_dimension(resolved_live, odds_bucket, "D.3: LIVE_VALUE BY ODDS RANGE")
        analyze_dimension(resolved_live, conf_bucket, "D.4: LIVE_VALUE BY CONFIDENCE")
    else:
        print("  No LIVE_VALUE signals with resolved scores.")

    # ═══════════════════════════════════════════════════════════════
    # E. INDIVIDUAL MATCH RESULTS (ZLATNA pre-match)
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "#" * 100)
    print("  SECTION E: INDIVIDUAL MATCH RESULTS — ZLATNA PRE-MATCH (STEAM+LATE_SHARP)")
    print("#" * 100)
    
    for b in sorted(zlatna_prematch_quality, key=lambda x: (x['date'], x['kick_off'])):
        emoji = "✅" if b['result'] == 'WON' else "❌"
        print(f"  {emoji} [{b['type']:<11}] {b['date']} {b['kick_off']:>5} | "
              f"{b['match'][:40]:<40} | {b['bet_label'][:20]:<20} "
              f"@ {b['odds']:.2f} (drop {b['drop_pct']:+.1f}%) | "
              f"Score: {b['final_score']} | {b['result']}")

    # ═══════════════════════════════════════════════════════════════
    # F. MOVEMENT TRAJECTORY ANALYSIS (odds path from first→last snapshot)
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "#" * 100)
    print("  SECTION F: ZLATNA MATCH ODDS TRAJECTORY (first vs last snapshot in movement_log)")
    print("#" * 100)
    
    # For each ZLATNA pre-match bet, find the full odds history from movement_log
    print("\n  Checking if STEAM moves continued or reversed before kick-off...")
    
    # Build a richer lookup from movement_log: (home_norm, away_norm) -> list of (scraped_at, odds_1, odds_x, odds_2)
    trajectory = defaultdict(list)
    with open(MOVEMENT_LOG, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            home = normalize_team(row.get('home', ''))
            away = normalize_team(row.get('away', ''))
            try:
                odds_1 = float(row.get('odds_1', 0))
                odds_x = float(row.get('odds_x', 0))
                odds_2 = float(row.get('odds_2', 0))
            except:
                continue
            scraped_at = row.get('scraped_at', '')
            trajectory[(home, away)].append({
                'scraped_at': scraped_at,
                'odds_1': odds_1,
                'odds_x': odds_x,
                'odds_2': odds_2,
            })
    
    reversal_count = 0
    continued_count = 0
    total_trajectory = 0
    
    for b in zlatna_prematch_quality:
        match_str = b['match']
        if ' vs ' not in match_str:
            continue
        parts = match_str.split(' vs ', 1)
        home = normalize_team(parts[0])
        away = normalize_team(parts[1])
        
        key = (home, away)
        if key not in trajectory:
            # Try fuzzy
            found = False
            for (h, a), traj in trajectory.items():
                if (home in h or h in home) and (away in a or a in away):
                    key = (h, a)
                    found = True
                    break
            if not found:
                continue
        
        traj = trajectory[key]
        if len(traj) < 3:
            continue
        
        total_trajectory += 1
        
        # Get first and last odds for the bet outcome
        bet = b['bet']
        odds_key = f'odds_{bet.lower()}' if bet != 'X' else 'odds_x'
        
        first_odds = traj[0].get(odds_key, 0)
        last_odds = traj[-1].get(odds_key, 0)
        mid_odds = traj[len(traj)//2].get(odds_key, 0)
        
        if first_odds > 0 and last_odds > 0:
            total_drop = (last_odds - first_odds) / first_odds * 100
            
            # Check if odds reversed (went back up) after dropping
            min_odds = min(t.get(odds_key, 999) for t in traj)
            max_drop = (min_odds - first_odds) / first_odds * 100 if first_odds > 0 else 0
            
            # Did odds bounce back more than 50% of the drop?
            if max_drop < -3 and total_drop > max_drop * 0.5:
                reversal_count += 1
            else:
                continued_count += 1

    if total_trajectory > 0:
        print(f"  Trajectories analyzed: {total_trajectory}")
        print(f"  Continued dropping: {continued_count} ({continued_count/total_trajectory:.1%})")
        print(f"  Reversed (bounced back): {reversal_count} ({reversal_count/total_trajectory:.1%})")

    # ═══════════════════════════════════════════════════════════════
    # G. SUMMARY & RECOMMENDATIONS
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "#" * 100)
    print("  SECTION G: SUMMARY & PARAMETER TWEAK RECOMMENDATIONS")
    print("#" * 100)

    # Current ZLATNA rules assessment
    print(f"\n  CURRENT ZLATNA PRAVILA PARAMETERS:")
    print(f"    ZLATNA_MIN_CONFIDENCE = 0.80")
    print(f"    ZLATNA_MIN_DROP_PCT   = 0.08  (8%)")
    print(f"    ZLATNA_MIN_SNAPSHOTS  = 30")
    print(f"    ZLATNA_ALLOWED_TYPES  = STEAM, LATE_SHARP")
    print(f"    ZLATNA_MAX_DRAW_ODDS  = 5.0")
    
    # Calculate current filter performance from ZLATNA data
    current_filter = zlatna_prematch_quality  # This IS the current filter output
    current_stats = calc_stats(current_filter)
    
    print(f"\n  Current filter performance (from ZLATNA_PRAVILA files):")
    print_stats("CURRENT ZLATNA (all)", current_stats)
    
    # Sub-breakdowns
    current_da = calc_stats([b for b in current_filter if b['consensus'] == 'DA'])
    current_ne = calc_stats([b for b in current_filter if b['consensus'] == 'NE'])
    current_no_x = calc_stats([b for b in current_filter if b['bet'] != 'X'])
    current_low_odds = calc_stats([b for b in current_filter if b['odds'] <= 3.0])
    current_high_conf = calc_stats([b for b in current_filter if b['confidence'] >= 0.90])
    current_high_drop = calc_stats([b for b in current_filter if abs(b['drop_pct']) >= 15])
    current_high_score = calc_stats([b for b in current_filter if b['score_val'] >= 75])
    
    print_stats("  └ With consensus (DA)", current_da)
    print_stats("  └ No consensus (NE)", current_ne)
    print_stats("  └ Excluding draws", current_no_x)
    print_stats("  └ Odds ≤ 3.00", current_low_odds)
    print_stats("  └ Conf ≥ 90%", current_high_conf)
    print_stats("  └ Drop ≥ 15%", current_high_drop)
    print_stats("  └ Score ≥ 75", current_high_score)

    # Suggest better if found
    print(f"\n  ── Suggested tweaks based on data ──")
    
    # From FINAL_BETS (larger dataset, no snapshot filter)
    print(f"\n  Using FINAL_BETS data (STEAM+LATE_SHARP, {len(steam_ls)} bets):")
    
    opt1 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 8])
    opt2 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 8 and b['consensus'] == 'DA'])
    opt3 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 8 and b['bet'] != 'X'])
    opt4 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 8 and b['odds'] <= 3.0])
    opt5 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 15])
    opt6 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 8 and b['consensus'] == 'DA' and b['bet'] != 'X'])
    opt7 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 8 and b['consensus'] == 'DA' and b['odds'] <= 3.0])
    opt8 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 15 and b['consensus'] == 'DA'])
    opt9 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 8 and b['bet'] != 'X' and b['odds'] <= 3.0])
    opt10 = calc_stats([b for b in steam_ls if b['confidence'] >= 0.90 and abs(b['drop_pct']) >= 15 and b['bet'] != 'X'])
    
    print_stats("Opt1: Conf≥90% Drop≥8%", opt1)
    print_stats("Opt2: Conf≥90% Drop≥8% Cons=DA", opt2)
    print_stats("Opt3: Conf≥90% Drop≥8% NoDraw", opt3)
    print_stats("Opt4: Conf≥90% Drop≥8% Odds≤3", opt4)
    print_stats("Opt5: Conf≥90% Drop≥15%", opt5)
    print_stats("Opt6: Conf≥90% Drop≥8% DA+NoDraw", opt6)
    print_stats("Opt7: Conf≥90% Drop≥8% DA+Odds≤3", opt7)
    print_stats("Opt8: Conf≥90% Drop≥15% DA", opt8)
    print_stats("Opt9: Conf≥90% Drop≥8% NoDraw+Odds≤3", opt9)
    print_stats("Opt10: Conf≥90% Drop≥15% NoDraw", opt10)
    
    # From ZLATNA data (has snapshots)
    print(f"\n  Using ZLATNA data (has snapshots, {len(zlatna_prematch_quality)} bets):")
    
    z1 = calc_stats([b for b in zlatna_prematch_quality if b['confidence'] >= 0.90])
    z2 = calc_stats([b for b in zlatna_prematch_quality if b['confidence'] >= 0.90 and b['consensus'] == 'DA'])
    z3 = calc_stats([b for b in zlatna_prematch_quality if b['confidence'] >= 0.90 and b['bet'] != 'X'])
    z4 = calc_stats([b for b in zlatna_prematch_quality if b['confidence'] >= 0.90 and b['consensus'] == 'DA' and b['bet'] != 'X'])
    z5 = calc_stats([b for b in zlatna_prematch_quality if b['confidence'] >= 0.90 and b['snapshots'] >= 50])
    z6 = calc_stats([b for b in zlatna_prematch_quality if b['confidence'] >= 0.90 and b['snapshots'] >= 50 and b['consensus'] == 'DA'])
    z7 = calc_stats([b for b in zlatna_prematch_quality if b['confidence'] >= 0.90 and b['score_val'] >= 75])
    z8 = calc_stats([b for b in zlatna_prematch_quality if b['confidence'] >= 0.90 and b['score_val'] >= 75 and b['consensus'] == 'DA'])
    z9 = calc_stats([b for b in zlatna_prematch_quality if b['consensus'] == 'DA' and b['bet'] != 'X' and b['odds'] <= 3.0])
    z10 = calc_stats([b for b in zlatna_prematch_quality if b['confidence'] >= 0.90 and b['consensus'] == 'DA' and b['bet'] != 'X' and b['odds'] <= 3.0])
    
    print_stats("Z1: Conf≥90%", z1)
    print_stats("Z2: Conf≥90% DA", z2)
    print_stats("Z3: Conf≥90% NoDraw", z3)
    print_stats("Z4: Conf≥90% DA+NoDraw", z4)
    print_stats("Z5: Conf≥90% Snaps≥50", z5)
    print_stats("Z6: Conf≥90% Snaps≥50 DA", z6)
    print_stats("Z7: Conf≥90% Score≥75", z7)
    print_stats("Z8: Conf≥90% Score≥75 DA", z8)
    print_stats("Z9: DA+NoDraw+Odds≤3 (any conf)", z9)
    print_stats("Z10: Conf≥90% DA+NoDraw+Odds≤3", z10)

    print(f"\n{'=' * 100}")
    print(f"  END OF ANALYSIS")
    print(f"{'=' * 100}\n")


if __name__ == '__main__':
    run_analysis()
