#!/usr/bin/env python3
"""
Match TSV signals against ALL finished matches from movement CSVs.
Uses aggressive fuzzy matching to maximize coverage.
"""
import pandas as pd
from pathlib import Path
import re
from difflib import SequenceMatcher

def normalize(s):
    """Normalize team name for matching."""
    if not s or pd.isna(s):
        return ""
    s = str(s).lower().strip()
    # Remove common suffixes
    s = re.sub(r'\s+(w|u21|u23|u19|ii|b|fc|sc|ac|cf)\s*$', '', s)
    s = re.sub(r'\s+(women|res|reserves|youth)\s*$', '', s)
    # Remove punctuation
    s = re.sub(r'[.\-\'"`()]', ' ', s)
    # Normalize spaces
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def get_key_words(s):
    """Extract significant words from team name."""
    norm = normalize(s)
    words = norm.split()
    # Filter out common generic words
    skip = {'fc', 'sc', 'ac', 'cf', 'cd', 'sd', 'ud', 'rc', 'real', 'club', 'sporting', 'athletic', 'de', 'la', 'el', 'los', 'las'}
    return [w for w in words if len(w) > 2 and w not in skip]

def fuzzy_match(s1, s2, threshold=0.75):
    """Check if two strings are similar enough."""
    n1, n2 = normalize(s1), normalize(s2)
    if not n1 or not n2:
        return False
    # Exact match after normalization
    if n1 == n2:
        return True
    # One contains the other
    if n1 in n2 or n2 in n1:
        return True
    # Shared key words
    words1, words2 = set(get_key_words(s1)), set(get_key_words(s2))
    if words1 and words2:
        common = words1 & words2
        if common and len(common) >= min(len(words1), len(words2)):
            return True
        if len(common) >= 1 and len(words1) <= 2 and len(words2) <= 2:
            return True
    # Sequence matcher
    ratio = SequenceMatcher(None, n1, n2).ratio()
    if ratio >= threshold:
        return True
    # Partial word match (first 5+ chars)
    if len(n1) >= 5 and len(n2) >= 5:
        if n1[:5] == n2[:5]:
            return True
    return False

def main():
    # Load TSV
    tsv_path = Path('odds_data/signal_map/ONLY_HOME_upcoming.tsv')
    tsv = pd.read_csv(tsv_path, sep='\t')
    print(f"TSV signals: {len(tsv)}")
    
    # Extract team pairs from TSV
    tsv_matches = []
    for _, row in tsv.iterrows():
        match = str(row.get('match', ''))
        if ' vs ' in match:
            home, away = match.split(' vs ', 1)
            tsv_matches.append({
                'home': home.strip(),
                'away': away.strip(),
                'kick_off': row.get('kick_off', ''),
                'odds': row.get('odds', 0),
                'strategies': row.get('strategies', ''),
                'confidence': row.get('confidence', ''),
            })
    
    # Load ALL finished matches from ALL CSV files
    csv_dir = Path('odds_data/movement')
    csv_files = sorted(csv_dir.glob('*.csv'))
    print(f"Scanning {len(csv_files)} CSV files...")
    
    finished = {}  # key: (home, away) normalized -> (home, away, score, csv_file)
    
    for fp in csv_files:
        try:
            df = pd.read_csv(fp, sep=';', dtype=str)
            
            # Handle different CSV formats
            if 'status' not in df.columns:
                continue
            
            df_fin = df[df['status'] == 'finished']
            
            for _, row in df_fin.iterrows():
                home = str(row.get('home', '')).strip()
                away = str(row.get('away', '')).strip()
                score = str(row.get('score', '')).strip()
                
                if not home or not away:
                    continue
                if not score or score == 'nan' or score == '':
                    continue
                
                # Store with normalized key
                key = (normalize(home), normalize(away))
                if key not in finished:
                    finished[key] = (home, away, score, fp.name)
        except:
            continue
    
    print(f"Finished matches with scores: {len(finished)}")
    
    # Match TSV signals against finished matches
    results = []
    unmatched = []
    
    for tsv_row in tsv_matches:
        home_tsv = tsv_row['home']
        away_tsv = tsv_row['away']
        home_norm = normalize(home_tsv)
        away_norm = normalize(away_tsv)
        
        found = False
        
        # Strategy 1: Exact normalized match
        key = (home_norm, away_norm)
        if key in finished:
            h, a, score, csv_file = finished[key]
            results.append({**tsv_row, 'score': score, 'csv': csv_file, 'method': 'exact'})
            found = True
            continue
        
        # Strategy 2: Fuzzy match
        for (fn_h, fn_a), (h, a, score, csv_file) in finished.items():
            if fuzzy_match(home_tsv, h) and fuzzy_match(away_tsv, a):
                results.append({**tsv_row, 'score': score, 'csv': csv_file, 'method': 'fuzzy'})
                found = True
                break
        
        if not found:
            unmatched.append(tsv_row)
    
    # Print results
    print()
    print("=" * 100)
    print(f"ONLY_HOME VERIFIED RESULTS ({len(results)} matches)")
    print("=" * 100)
    
    wins, losses, draws = 0, 0, 0
    profit = 0.0
    
    for r in sorted(results, key=lambda x: str(x.get('kick_off', ''))):
        score = r['score']
        odds = float(r['odds']) if r['odds'] else 0
        
        # Parse score and determine outcome
        home_goals, away_goals = 0, 0
        if '-' in score:
            parts = score.split('-')
            try:
                home_goals = int(parts[0].strip())
                away_goals = int(parts[1].strip())
            except:
                pass
        elif ':' in score:
            parts = score.split(':')
            try:
                home_goals = int(parts[0].strip())
                away_goals = int(parts[1].strip())
            except:
                pass
        
        if home_goals > away_goals:
            symbol = "✅"
            wins += 1
            unit_profit = odds - 1
        elif home_goals < away_goals:
            symbol = "❌"
            losses += 1
            unit_profit = -1.0
        else:
            symbol = "⚪"
            draws += 1
            unit_profit = 0.0
        
        profit += unit_profit
        
        match_str = f"{r['home']} vs {r['away']}"
        print(f"{symbol} {r['kick_off']:5} | {match_str:45} | {score:5} | @{odds:.2f} | {unit_profit:+.2f}u | {r['strategies']}")
    
    # Summary
    print()
    print("=" * 100)
    print("PERFORMANCE SUMMARY")
    print("=" * 100)
    
    total_verified = len(results)
    total_signals = len(tsv_matches)
    coverage = (total_verified / total_signals * 100) if total_signals else 0
    wl_total = wins + losses
    win_rate = (wins / wl_total * 100) if wl_total else 0
    roi = (profit / total_verified * 100) if total_verified else 0
    avg_odds = sum(float(r['odds']) for r in results if r['odds']) / len(results) if results else 0
    
    print(f"Coverage:  {total_verified} / {total_signals} signals ({coverage:.1f}%)")
    print(f"Record:    {wins}W / {losses}L / {draws}D")
    print(f"Profit:    {profit:+.2f}u (flat 1u stakes)")
    print(f"ROI:       {roi:.1f}%")
    print(f"Win Rate:  {win_rate:.1f}% (excl. draws)")
    print(f"Avg Odds:  {avg_odds:.2f}")
    
    # COMBO subset
    combo_results = [r for r in results if 'COMBO' in str(r.get('strategies', ''))]
    if combo_results:
        cw, cl, cp = 0, 0, 0.0
        for r in combo_results:
            score = r['score']
            odds = float(r['odds']) if r['odds'] else 0
            try:
                if '-' in score:
                    h, a = int(score.split('-')[0]), int(score.split('-')[1])
                else:
                    h, a = int(score.split(':')[0]), int(score.split(':')[1])
                if h > a:
                    cw += 1
                    cp += (odds - 1)
                elif h < a:
                    cl += 1
                    cp -= 1
            except:
                pass
        print(f"\n--- COMBO Strategy ({len(combo_results)} bets) ---")
        if (cw+cl) > 0:
            print(f"Record: {cw}W / {cl}L | Profit: {cp:+.2f}u | Win Rate: {cw/(cw+cl)*100:.1f}%")
    
    # Unmatched
    print()
    print("=" * 100)
    print(f"PENDING ({len(unmatched)} signals - unmatched)")
    print("=" * 100)
    for u in sorted(unmatched, key=lambda x: str(x.get('kick_off', '')))[:25]:
        print(f"  {u['kick_off']:5} | {u['home']} vs {u['away']} | @{u['odds']}")
    if len(unmatched) > 25:
        print(f"  ... +{len(unmatched)-25} more")

if __name__ == '__main__':
    main()
