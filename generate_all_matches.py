#!/usr/bin/env python3
"""
Generate ALL_MATCHES TSV with odds movements for all tracked matches.
Run manually whenever you need fresh data.

Usage: python generate_all_matches.py
"""
import pandas as pd
from pathlib import Path
from collections import defaultdict
from datetime import datetime

def main():
    csv_dir = Path('odds_data/movement')
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Find today's CSV files
    today_files = sorted(csv_dir.glob(f'{today}*.csv'))
    if not today_files:
        # Fallback to most recent date
        all_files = sorted(csv_dir.glob('*.csv'))
        if all_files:
            latest_date = all_files[-1].name[:10]
            today_files = sorted(csv_dir.glob(f'{latest_date}*.csv'))
            today = latest_date
    
    print(f'📊 Generiranje ALL_MATCHES za {today}')
    print(f'   Učitavam {len(today_files)} CSV fajlova...')
    
    # Collect first and last snapshot for each match
    matches = defaultdict(lambda: {'first': None, 'last': None, 'count': 0})
    
    for fp in today_files:
        try:
            df = pd.read_csv(fp, sep=';', dtype=str)
            for _, r in df.iterrows():
                key = (r.get('home', ''), r.get('away', ''))
                if not key[0]:
                    continue
                matches[key]['count'] += 1
                matches[key]['last'] = r
                if matches[key]['first'] is None:
                    matches[key]['first'] = r
        except Exception:
            continue
    
    # Build rows
    rows = []
    for key, data in matches.items():
        if data['count'] < 10:
            continue
        f, l = data['first'], data['last']
        if l.get('status') != 'upcoming':
            continue
        
        try:
            o1 = float(f.get('odds_1', 0) or 0)
            l1 = float(l.get('odds_1', 0) or 0)
            ox = float(f.get('odds_x', 0) or 0)
            lx = float(l.get('odds_x', 0) or 0)
            o2 = float(f.get('odds_2', 0) or 0)
            l2 = float(l.get('odds_2', 0) or 0)
        except Exception:
            continue
        
        pct1 = (l1 - o1) / o1 * 100 if o1 > 0 else 0
        pctx = (lx - ox) / ox * 100 if ox > 0 else 0
        pct2 = (l2 - o2) / o2 * 100 if o2 > 0 else 0
        
        steam = []
        if abs(pct1) >= 10:
            steam.append('1')
        if abs(pctx) >= 10:
            steam.append('X')
        if abs(pct2) >= 10:
            steam.append('2')
        
        match_date = l.get('match_date', today)
        kick_off = l.get('kick_off', '00:00')
        
        rows.append({
            'date': match_date,
            'kick_off': kick_off,
            'country': l.get('country', ''),
            'league': l.get('league', ''),
            'home': key[0],
            'away': key[1],
            'open_1': round(o1, 2) if o1 else '',
            'latest_1': round(l1, 2) if l1 else '',
            'change_1': f'{pct1:+.1f}%',
            'open_X': round(ox, 2) if ox else '',
            'latest_X': round(lx, 2) if lx else '',
            'change_X': f'{pctx:+.1f}%',
            'open_2': round(o2, 2) if o2 else '',
            'latest_2': round(l2, 2) if l2 else '',
            'change_2': f'{pct2:+.1f}%',
            'snapshots': data['count'],
            'steam_on': ','.join(steam) if steam else ''
        })
    
    # Create DataFrame and sort
    df_out = pd.DataFrame(rows)
    df_out = df_out.sort_values(['date', 'kick_off'])
    
    # Save
    out_path = Path(f'odds_data/signal_map/ALL_MATCHES_{today}.tsv')
    df_out.to_csv(out_path, sep='\t', index=False)
    
    steam_count = len([r for r in rows if r['steam_on']])
    
    print(f'')
    print(f'✅ Spremljeno: {out_path}')
    print(f'   Ukupno: {len(df_out)} utakmica')
    print(f'   Sa STEAM (>=10%): {steam_count}')
    print(f'   Sortirano po: date → kick_off')

if __name__ == '__main__':
    main()
