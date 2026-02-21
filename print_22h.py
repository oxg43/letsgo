import pandas as pd

df = pd.read_csv('odds_data/signal_map/MATCHES_22h_2026-02-21.tsv', sep='\t', dtype=str)
df = df.sort_values(['date', 'kick_off'])

current_date = None
for _, r in df.iterrows():
    if r['date'] != current_date:
        current_date = r['date']
        print()
        print('='*70)
        print(f"  {current_date}")
        print('='*70)
    
    steam = ' *STEAM*' if r.get('steam_on') else ''
    c1 = r['change_1'] if r['change_1'] != '+0.0%' else ''
    cX = r['change_X'] if r['change_X'] != '+0.0%' else ''
    c2 = r['change_2'] if r['change_2'] != '+0.0%' else ''
    changes = f"{c1:>8} {cX:>8} {c2:>8}" if any([c1,cX,c2]) else ''
    print(f"{r['kick_off']} | {r['home'][:22]:<22} vs {r['away'][:22]:<22} {changes}{steam}")
