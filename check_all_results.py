#!/usr/bin/env python3
"""
Comprehensive ONLY_HOME results finder.
Searches ALL CSV files for results - no date filtering.
"""
import pandas as pd
from pathlib import Path
import re

def normalize(name):
    """Normalize team name for matching."""
    n = name.lower().strip()
    # Remove common suffixes
    for s in [' fc', ' sc', ' u21', ' u23', ' w', ' (w)', '.', ' utd', ' united', ' city', ' u.']:
        n = n.replace(s, '')
    return re.sub(r'[^\w\s]', '', n).strip()

def main():
    # Load TSV
    tsv = pd.read_csv('odds_data/signal_map/ONLY_HOME_upcoming.tsv', sep='\t')
    print(f"TSV signals: {len(tsv)}")
    
    # Load ALL CSV files - no date filter
    csv_dir = Path('odds_data/movement')
    csv_files = sorted(csv_dir.glob('*.csv'))
    print(f"Total CSV files to search: {len(csv_files)}")
    
    # Build comprehensive finished matches database
    finished = {}
    for fp in csv_files:
        try:
            df = pd.read_csv(fp, sep=';', dtype=str)
            if 'status' not in df.columns:
                continue
            
            # Check for score column
            score_col = 'score' if 'score' in df.columns else None
            if not score_col:
                continue
                
            df_fin = df[df['status'] == 'finished']
            for _, row in df_fin.iterrows():
                home = str(row.get('home', '')).strip()
                away = str(row.get('away', '')).strip()
                score = str(row.get(score_col, '')).strip()
                
                if not home or not away or not score or score == 'nan' or score == '':
                    continue
                
                # Store with multiple key formats for better matching
                key1 = f"{home} vs {away}"
                key2 = f"{normalize(home)} vs {normalize(away)}"
                finished[key1] = score
                finished[key2] = score
        except Exception as e:
            continue
    
    unique_matches = len(set(v for v in finished.values()))
    print(f"Finished matches with scores found: {len(finished)//2}")
    
    # Match TSV signals with finished results
    results = []
    pending = []
    
    for _, row in tsv.iterrows():
        match = str(row['match'])
        kick_off = str(row['kick_off'])
        odds = float(row['odds'])
        strategies = str(row.get('strategies', ''))
        confidence = str(row.get('confidence', ''))
        
        if ' vs ' not in match:
            pending.append({'match': match, 'kick_off': kick_off, 'odds': odds})
            continue
        
        parts = match.split(' vs ')
        home = parts[0].strip()
        away = parts[1].strip()
        
        score = None
        
        # Method 1: Exact match
        if match in finished:
            score = finished[match]
        
        # Method 2: Normalized key
        nkey = f"{normalize(home)} vs {normalize(away)}"
        if not score and nkey in finished:
            score = finished[nkey]
        
        # Method 3: Partial matching (first 6 chars of each team)
        if not score:
            norm_home = normalize(home)
            norm_away = normalize(away)
            for k, v in finished.items():
                if ' vs ' not in k:
                    continue
                kparts = k.split(' vs ')
                if len(kparts) < 2:
                    continue
                kh = normalize(kparts[0])
                ka = normalize(kparts[1])
                
                # Check if first 6 characters match OR one contains the other
                home_match = (len(norm_home) >= 6 and len(kh) >= 6 and norm_home[:6] == kh[:6]) or \
                             norm_home in kh or kh in norm_home
                away_match = (len(norm_away) >= 6 and len(ka) >= 6 and norm_away[:6] == ka[:6]) or \
                             norm_away in ka or ka in norm_away
                
                if home_match and away_match:
                    score = v
                    break
        
        if score:
            # Parse score
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
        
        pending.append({'match': match, 'kick_off': kick_off, 'odds': odds})
    
    # Sort by kick_off
    results.sort(key=lambda x: x['kick_off'])
    
    # Calculate stats
    wins = sum(1 for r in results if r['outcome'] == 'WIN')
    losses = sum(1 for r in results if r['outcome'] == 'LOSS')
    draws = sum(1 for r in results if r['outcome'] == 'DRAW')
    total_profit = sum(r['profit'] for r in results)
    
    # Print detailed results
    print(f"\n{'='*130}")
    print(f"ONLY_HOME VERIFIED RESULTS ({len(results)} / {len(tsv)} matches)")
    print(f"{'='*130}")
    
    for r in results:
        emoji = '✅' if r['outcome'] == 'WIN' else ('❌' if r['outcome'] == 'LOSS' else '⚪')
        strat = r['strategies'][:20] if r['strategies'] else ''
        conf = r['confidence'] if r['confidence'] else ''
        print(f"{emoji} {r['kick_off']:5} | {r['match'][:50]:<50} | {r['score']:5} | @{r['odds']:.2f} | {r['profit']:+.2f}u | {strat}")
    
    # Summary
    print(f"\n{'='*130}")
    print(f"PERFORMANCE SUMMARY")
    print(f"{'='*130}")
    print(f"Coverage:  {len(results)} / {len(tsv)} signals ({len(results)/len(tsv)*100:.1f}%)")
    print(f"Record:    {wins}W / {losses}L / {draws}D")
    print(f"Profit:    {total_profit:+.2f}u (flat 1u stakes)")
    
    if len(results) > 0:
        print(f"ROI:       {total_profit/len(results)*100:.1f}%")
        print(f"Avg Odds:  {sum(r['odds'] for r in results)/len(results):.2f}")
    if wins + losses > 0:
        print(f"Win Rate:  {wins/(wins+losses)*100:.1f}% (excluding draws)")
    
    # COMBO subset
    combo = [r for r in results if 'COMBO' in str(r['strategies'])]
    if combo:
        cw = sum(1 for c in combo if c['outcome'] == 'WIN')
        cl = sum(1 for c in combo if c['outcome'] == 'LOSS')
        cd = sum(1 for c in combo if c['outcome'] == 'DRAW')
        cp = sum(c['profit'] for c in combo)
        print(f"\n--- COMBO Strategy ({len(combo)} bets) ---")
        print(f"Record: {cw}W / {cl}L / {cd}D | Profit: {cp:+.2f}u", end='')
        if cw + cl > 0:
            print(f" | Win Rate: {cw/(cw+cl)*100:.1f}%")
        else:
            print()
    
    # By confidence
    print(f"\n--- By Confidence Level ---")
    for conf in ['100%', '95%', '85%', '80%', '70%', '65%', '55%', '50%']:
        subset = [r for r in results if conf in str(r['confidence'])]
        if subset:
            sw = sum(1 for s in subset if s['outcome'] == 'WIN')
            sl = sum(1 for s in subset if s['outcome'] == 'LOSS')
            sd = sum(1 for s in subset if s['outcome'] == 'DRAW')
            sp = sum(s['profit'] for s in subset)
            wr = sw/(sw+sl)*100 if sw+sl > 0 else 0
            print(f"{conf:5}: {sw}W/{sl}L/{sd}D | Profit: {sp:+.2f}u | Win Rate: {wr:.0f}%")
    
    # Pending
    print(f"\n{'='*130}")
    print(f"PENDING ({len(pending)} signals - no result found in database)")
    print(f"{'='*130}")
    pending.sort(key=lambda x: x['kick_off'])
    for p in pending[:25]:
        print(f"  {p['kick_off']:5} | {p['match'][:60]} | @{p['odds']:.2f}")
    if len(pending) > 25:
        print(f"  ... +{len(pending)-25} more pending")

if __name__ == "__main__":
    main()
