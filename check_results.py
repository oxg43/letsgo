#!/usr/bin/env python3
"""Check ONLY_HOME results"""

import pandas as pd
from pathlib import Path

# Load TSV
tsv = pd.read_csv('odds_data/signal_map/ONLY_HOME_upcoming.tsv', sep='\t')
print(f"Ukupno signala: {len(tsv)}")

# Load movement data for results
DATA_DIR = Path('odds_data/movement')
csv_files = sorted(DATA_DIR.glob('*.csv'))[-50:]  # More files

frames = []
for fp in csv_files:
    df = pd.read_csv(fp, sep=';', dtype=str)
    frames.append(df)

results = pd.concat(frames, ignore_index=True)
results_finished = results[results['status'] == 'finished'].drop_duplicates(subset=['home', 'away'], keep='last')
print(f"Zavrsenih meceva u bazi: {len(results_finished)}")

# Show samples
print("\n=== TSV PRIMJERI ===")
for _, row in tsv.head(5).iterrows():
    print(f"  {row['match'][:50]}")

print("\n=== CSV ZAVRSENI PRIMJERI ===")
for _, row in results_finished.head(10).iterrows():
    print(f"  {row['home']} vs {row['away']} = {row['score']}")

# Try matching
wins, losses, draws = 0, 0, 0
checked = []

for _, row in tsv.iterrows():
    match = str(row['match'])
    if ' vs ' not in match:
        continue
    parts = match.split(' vs ')
    home = parts[0].strip()
    away = parts[1].strip()
    
    # Find result - try different matching approaches
    found = results_finished[
        (results_finished['home'].str.strip() == home) & 
        (results_finished['away'].str.strip() == away)
    ]
    
    if len(found) == 0:
        # Try partial match (escape regex special chars)
        import re
        home_esc = re.escape(home[:10])
        away_esc = re.escape(away[:10])
        found = results_finished[
            results_finished['home'].str.contains(home_esc, case=False, na=False, regex=True) &
            results_finished['away'].str.contains(away_esc, case=False, na=False, regex=True)
        ]
    
    if len(found) == 0:
        continue
    
    score = str(found.iloc[0]['score'])
    # Handle both formats: "1:0" and "1-0"
    if '-' in score:
        score = score.replace('-', ':')
    if ':' not in score or score == 'nan':
        continue
    
    try:
        h_goals, a_goals = map(int, score.split(':'))
        
        if h_goals > a_goals:
            wins += 1
            tip = 'WIN'
            profit = float(row['odds']) - 1
        elif h_goals < a_goals:
            losses += 1
            tip = 'LOSS'
            profit = -1
        else:
            draws += 1
            tip = 'DRAW'
            profit = 0
            
        checked.append({
            'match': match[:40],
            'score': score,
            'odds': float(row['odds']),
            'tip': tip,
            'profit': profit,
            'strategies': str(row['strategies'])[:15],
            'confidence': str(row['confidence'])
        })
    except Exception as e:
        continue

print(f"\n{'='*50}")
print(f"=== REZULTATI ONLY_HOME ===")
print(f"{'='*50}")
print(f"Provjereno meceva: {len(checked)}")
print(f"✅ WIN:  {wins}")
print(f"❌ LOSS: {losses}")
print(f"⚪ DRAW: {draws}")

if checked:
    total_profit = sum(c['profit'] for c in checked)
    roi = total_profit / len(checked) * 100
    win_rate = wins / len(checked) * 100
    
    print(f"\nUkupni profit: {total_profit:.2f}u")
    print(f"ROI: {roi:.1f}%")
    print(f"Win rate: {win_rate:.1f}%")
    
    print(f"\n{'='*50}")
    print("DETALJI ZAVRSENIH:")
    print(f"{'='*50}")
    for c in checked:
        marker = '✅' if c['tip'] == 'WIN' else ('❌' if c['tip'] == 'LOSS' else '⚪')
        print(f"{marker} {c['match']:40} | {c['score']:5} | {c['odds']:.2f} | {c['profit']:+.2f}u | {c['strategies']}")
