import pandas as pd

old = pd.read_csv('odds_data/signal_map/R7_SIGNALS_2026-02-24.tsv', sep='\t')

# Regenerate fresh with R7
from generate_all_matches import generate_all_matches_tsv
tsv_path, total, steam = generate_all_matches_tsv(verbose=False)
new_all = pd.read_csv(tsv_path, sep='\t')
new = new_all[new_all['R7'].notna() & (new_all['R7']!='')].copy()

old_keys = set(old['home'] + ' vs ' + old['away'] + ' | ' + old['R7'])
new_keys = set(new['home'] + ' vs ' + new['away'] + ' | ' + new['R7'])

lost = old_keys - new_keys
gained = new_keys - old_keys

print(f'RANIJE: {len(old)} R7 signala')
print(f'SADA:   {len(new)} R7 signala')
print()

if lost:
    print(f'--- NESTALI ({len(lost)}): ---')
    for m in sorted(lost):
        print(f'  - {m}')
    print()

if gained:
    print(f'+++ NOVI ({len(gained)}): +++')
    for m in sorted(gained):
        print(f'  + {m}')
    print()

# Check odds changes for matches still in R7
print('--- PROMJENE ODDSA (matches koji su ostali u R7): ---')
changes = []
for _, r in new.iterrows():
    old_match = old[(old['home']==r['home']) & (old['away']==r['away']) & (old['R7']==r['R7'])]
    if len(old_match) == 1:
        o = old_match.iloc[0]
        r7side = r['R7'].split('@')[0]
        col = {'HOME':'latest_1','DRAW':'latest_X','AWAY':'latest_2'}[r7side]
        old_odds = o[col]
        new_odds = r[col]
        old_q = o['R7_Q']
        new_q = r['R7_Q']
        if old_odds != new_odds or old_q != new_q:
            changes.append(f"  {r['home']} vs {r['away']} [{r['R7']}]: odds {old_odds}->{new_odds}, Q{old_q:.0f}->Q{new_q:.0f}")

if changes:
    for c in changes:
        print(c)
else:
    print('  Nema promjena oddsa')

# Update R7_SIGNALS file
new.to_csv('odds_data/signal_map/R7_SIGNALS_2026-02-24.tsv', sep='\t', index=False)
print(f'\nR7_SIGNALS_2026-02-24.tsv updated ({len(new)} signals)')
