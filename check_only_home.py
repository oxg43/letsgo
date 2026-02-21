#!/usr/bin/env python3
"""
Direct TSV-to-CSV matcher for ONLY_HOME signals.
Uses proper Feb 20 CSV format.
"""
import pandas as pd
from pathlib import Path
import glob
import re

def normalize(name):
    n = name.lower().strip()
    for s in [' fc', ' sc', ' u21', ' u23', ' w', '.', ' utd', ' united', ' city']:
        n = n.replace(s, '')
    return re.sub(r'[^\w\s]', '', n).strip()

def main():
    # Load TSV
    tsv = pd.read_csv('odds_data/signal_map/ONLY_HOME_upcoming.tsv', sep='\t')
    print(f"TSV signals: {len(tsv)}")
    
    # Load latest CSV files (Feb 20 only)
    csv_dir = Path('odds_data/movement')
    csv_files = list(csv_dir.glob('2026-02-20*.csv'))
    print(f"Found {len(csv_files)} Feb 20 CSV files")
    
    if not csv_files:
        csv_files = sorted(csv_dir.glob('*.csv'))[-50:]
    
    # Collect finished matches with scores
    finished = {}
    for fp in csv_files:
        try:
            df = pd.read_csv(fp, sep=';', dtype=str)
            df_fin = df[df['status'] == 'finished']
            for _, row in df_fin.iterrows():
                home = str(row.get('home', '')).strip()
                away = str(row.get('away', '')).strip()
                score = str(row.get('score', '')).strip()
                if home and away and score and score != 'nan':
                    key = f"{home} vs {away}"
                    finished[key] = score
                    # Also normalized key
                    nkey = f"{normalize(home)} vs {normalize(away)}"
                    finished[nkey] = score
        except Exception as e:
            continue
    
    print(f"Finished matches with scores: {len(finished)//2}")  # div 2 due to normalized
    
    # Match TSV signals
    results = []
    pending = []
    
    for _, row in tsv.iterrows():
        match = str(row['match'])
        kick_off = str(row['kick_off'])
        odds = float(row['odds'])
        strategies = str(row.get('strategies', ''))
        confidence = str(row.get('confidence', ''))
        
        # Parse home/away from TSV match
        if ' vs ' not in match:
            pending.append((match, kick_off, odds))
            continue
        
        parts = match.split(' vs ')
        home = parts[0].strip()
        away = parts[1].strip()
        
        # Try to find score
        score = None
        
        # Method 1: exact match
        if match in finished:
            score = finished[match]
        # Method 2: normalized
        nkey = f"{normalize(home)} vs {normalize(away)}"
        if not score and nkey in finished:
            score = finished[nkey]
        # Method 3: partial home match
        if not score:
            for k, v in finished.items():
                if ' vs ' in k:
                    fh, fa = k.split(' vs ')[:2]
                    if ((normalize(home)[:8] == normalize(fh)[:8] or 
                         normalize(home) in normalize(fh) or 
                         normalize(fh) in normalize(home)) and
                        (normalize(away)[:8] == normalize(fa)[:8] or
                         normalize(away) in normalize(fa) or
                         normalize(fa) in normalize(away))):
                        score = v
                        break
        
        if score:
            # Parse score (format: "2-1" or "2:1")
            score = score.replace('-', ':')
            if ':' in score:
                try:
                    h_goals, a_goals = map(int, score.split(':')[:2])
                    if h_goals > a_goals:
                        outcome = 'WIN'
                        profit = odds - 1
                    elif h_goals < a_goals:
                        outcome = 'LOSS'
                        profit = -1
                    else:
                        outcome = 'DRAW'
                        profit = 0
                    
                    results.append({
                        'match': match,
                        'kick_off': kick_off,
                        'score': f"{h_goals}:{a_goals}",
                        'odds': odds,
                        'outcome': outcome,
                        'profit': profit,
                        'strategies': strategies,
                        'confidence': confidence
                    })
                    continue
                except:
                    pass
        
        pending.append((match, kick_off, odds))
    
    # Sort by kick_off
    results.sort(key=lambda x: x['kick_off'])
    
    # Calculate stats
    wins = sum(1 for r in results if r['outcome'] == 'WIN')
    losses = sum(1 for r in results if r['outcome'] == 'LOSS')
    draws = sum(1 for r in results if r['outcome'] == 'DRAW')
    total_profit = sum(r['profit'] for r in results)
    
    # Print results
    print(f"\n{'='*120}")
    print(f"ONLY_HOME VERIFIED RESULTS ({len(results)} matches)")
    print(f"{'='*120}")
    
    for r in results:
        emoji = '✅' if r['outcome'] == 'WIN' else ('❌' if r['outcome'] == 'LOSS' else '⚪')
        strat = r['strategies'][:18] if r['strategies'] else ''
        print(f"{emoji} {r['kick_off']:5} | {r['match'][:45]:<45} | {r['score']:5} | @{r['odds']:.2f} | {r['profit']:+.2f}u | {strat}")
    
    print(f"\n{'='*120}")
    print(f"PERFORMANCE SUMMARY")
    print(f"{'='*120}")
    print(f"Coverage:  {len(results)} / {len(tsv)} signals ({len(results)/len(tsv)*100:.1f}%)")
    print(f"Record:    {wins}W / {losses}L / {draws}D")
    print(f"Profit:    {total_profit:+.2f}u (flat 1u stakes)")
    if len(results) > 0:
        print(f"ROI:       {total_profit/len(results)*100:.1f}%")
    if wins + losses > 0:
        print(f"Win Rate:  {wins/(wins+losses)*100:.1f}% (excl. draws)")
        print(f"Avg Odds:  {sum(r['odds'] for r in results)/len(results):.2f}")
    
    # COMBO subset
    combo = [r for r in results if 'COMBO' in str(r['strategies'])]
    if combo:
        cw = sum(1 for c in combo if c['outcome'] == 'WIN')
        cl = sum(1 for c in combo if c['outcome'] == 'LOSS')
        cp = sum(c['profit'] for c in combo)
        print(f"\n--- COMBO Strategy ({len(combo)} bets) ---")
        print(f"Record: {cw}W / {cl}L | Profit: {cp:+.2f}u | Win Rate: {cw/(cw+cl)*100:.1f}%" if cw+cl > 0 else "")
    
    # By confidence
    print(f"\n--- By Confidence Level ---")
    for conf in ['100%', '95%', '85%', '80%', '70%']:
        subset = [r for r in results if conf in str(r['confidence'])]
        if subset:
            sw = sum(1 for s in subset if s['outcome'] == 'WIN')
            sl = sum(1 for s in subset if s['outcome'] == 'LOSS')
            sp = sum(s['profit'] for s in subset)
            wr = sw/(sw+sl)*100 if sw+sl > 0 else 0
            print(f"{conf:5}: {sw}W/{sl}L | {sp:+.2f}u | {wr:.0f}% WR")
    
    print(f"\n{'='*120}")
    print(f"PENDING ({len(pending)} signals - games not finished or not in database)")
    print(f"{'='*120}")
    pending.sort(key=lambda x: x[1])  # by kick_off
    for m, ko, o in pending[:20]:
        print(f"  {ko:5} | {m[:55]} | @{o:.2f}")
    if len(pending) > 20:
        print(f"  ... +{len(pending)-20} more")

if __name__ == "__main__":
    main()
