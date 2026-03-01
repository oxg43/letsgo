import pandas as pd
from trader import load_movement_data

df = load_movement_data(days_back=3)
print(f'Movement podataka: {len(df)} redova')

# Check specific R7 matches from 24.02 and 25.02
matches = ['Alfreton|Scarborough', 'Northampton|Port Vale', 'Metta|Grobina', 
           'Elgin City|Edinburgh City', 'Bedford|Peterborough Sports',
           'Warrenpoint|Ballinamallard', 'Alvechurch|Real Bedford']

for m in matches:
    rows = df[df['match_id'] == m]
    if len(rows) > 0:
        last = rows.sort_values('scraped_at').iloc[-1]
        st = last.get('status', '?')
        sc = last.get('score', '?')
        print(f'  {m}: status={st}, score={sc}, rows={len(rows)}')
    else:
        print(f'  {m}: NOT FOUND in movement data')

# Also check how many finished matches exist in general
if 'status' in df.columns:
    finished = df[df['status'].str.lower().str.strip() == 'finished']
    print(f'\nFinished matches in movement data: {len(finished)} rows')
    if len(finished) > 0:
        print(finished[['match_id','score','status']].drop_duplicates('match_id').head(20).to_string())
