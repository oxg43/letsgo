"""
Generate ZLATNA_PRAVILA retroactively from today's FINAL_BETS data.
Applies the golden rules to show what the best bets were today.
"""
import csv
import math
import re
from pathlib import Path

FINAL_PATH = Path("odds_data/signal_map/FINAL_BETS_2026-02-07.tsv")
ZLATNA_PATH = Path("odds_data/signal_map/ZLATNA_PRAVILA_2026-02-07.tsv")

# Golden rules thresholds
MIN_CONFIDENCE = 0.80
MIN_DROP_PCT = 0.08
MIN_SNAPSHOTS = 30
ALLOWED_TYPES = {'STEAM', 'LATE_SHARP'}
MAX_DRAW_ODDS = 5.0

ZLATNA_COLUMNS = [
    'captured_at', 'kick_off', 'min_to_ko', 'match', 'bet', 'odds', 'opening',
    'drop_pct', 'snapshots', 'confidence', 'score', 'market_consensus', 'type', 'reason',
]


def calc_score(drop_pct, confidence, snapshots, has_consensus):
    drop_score = min(abs(drop_pct) / 0.25, 1.0) * 40
    conf_score = confidence * 30
    data_score = min(math.sqrt(snapshots) / 10, 1.0) * 20
    cons_score = 10.0 if has_consensus else 0.0
    return round(drop_score + conf_score + data_score + cons_score, 1)


def extract_snapshots(reason: str) -> int:
    """Extract snapshot count from reason text."""
    m = re.search(r'(\d+)\s*snapshots?', reason)
    return int(m.group(1)) if m else 0


def main():
    if not FINAL_PATH.exists():
        print(f"FINAL_BETS file not found: {FINAL_PATH}")
        return

    rows = []
    with open(FINAL_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            rows.append(row)

    print(f"Loaded {len(rows)} FINAL_BETS entries")

    # Apply golden rules
    zlatna = []
    for row in rows:
        sig_type = row.get('type', '').strip()
        if sig_type not in ALLOWED_TYPES:
            continue

        # Parse confidence
        conf_str = row.get('confidence', '0%').strip().rstrip('%')
        try:
            confidence = float(conf_str) / 100
        except:
            continue
        if confidence < MIN_CONFIDENCE:
            continue

        # Parse drop
        change_str = row.get('change', '0%').strip().rstrip('%').replace('+', '')
        try:
            pct_change = float(change_str) / 100
        except:
            continue
        if abs(pct_change) < MIN_DROP_PCT:
            continue

        # Parse snapshots from reason
        reason = row.get('reason', '')
        snapshots = extract_snapshots(reason)
        if snapshots < MIN_SNAPSHOTS:
            continue

        # No extreme draw bets
        bet = row.get('bet', '')
        try:
            odds = float(row.get('odds', '0'))
        except:
            odds = 0
        if 'Draw' in bet and odds > MAX_DRAW_ODDS:
            continue

        # Calculate score
        has_consensus = 'market consensus' in reason.lower() or 'other outcomes rising' in reason.lower()
        score = calc_score(pct_change, confidence, snapshots, has_consensus)

        zlatna.append({
            'captured_at': row.get('captured_at', ''),
            'kick_off': row.get('kick_off', ''),
            'min_to_ko': row.get('min_to_ko', ''),
            'match': row.get('match', ''),
            'bet': bet,
            'odds': row.get('odds', ''),
            'opening': row.get('opening', ''),
            'drop_pct': row.get('change', ''),
            'snapshots': str(snapshots),
            'confidence': row.get('confidence', ''),
            'score': f"{score:.1f}",
            'market_consensus': 'DA' if has_consensus else 'NE',
            'type': sig_type,
            'reason': reason,
        })

    # Sort by score
    zlatna.sort(key=lambda r: float(r['score']), reverse=True)

    # Write
    with open(ZLATNA_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=ZLATNA_COLUMNS, delimiter='\t')
        writer.writeheader()
        writer.writerows(zlatna)

    print(f"\n✅ ZLATNA PRAVILA: {len(zlatna)} od {len(rows)} prosli filter ({len(zlatna)/len(rows)*100:.0f}%)")
    print(f"   Saved to: {ZLATNA_PATH}")

    print(f"\n{'='*90}")
    print(f"  {'KO':>5}  {'Score':>5}  {'Type':<11}  {'Match':<35}  {'Bet':<22}  {'Odds':>5}  {'Drop':>7}  {'Snaps':>5}  {'Cons':>3}")
    print(f"{'='*90}")
    for r in zlatna:
        print(
            f"  {r['kick_off']:>5}  "
            f"{r['score']:>5}  "
            f"{r['type']:<11}  "
            f"{r['match'][:35]:<35}  "
            f"{r['bet'][:22]:<22}  "
            f"{r['odds']:>5}  "
            f"{r['drop_pct']:>7}  "
            f"{r['snapshots']:>5}  "
            f"{r['market_consensus']:>3}"
        )

    # Stats
    scores = [float(r['score']) for r in zlatna]
    if scores:
        print(f"\n  Prosjek score: {sum(scores)/len(scores):.1f}")
        print(f"  Najbolji score: {max(scores):.1f}")
        print(f"  Najgori score: {min(scores):.1f}")

        # Type distribution
        types = {}
        for r in zlatna:
            t = r['type']
            types[t] = types.get(t, 0) + 1
        print(f"\n  Tipovi: {types}")

        consensus_count = sum(1 for r in zlatna if r['market_consensus'] == 'DA')
        print(f"  Market consensus: {consensus_count}/{len(zlatna)} ({consensus_count/len(zlatna)*100:.0f}%)")


if __name__ == '__main__':
    main()
