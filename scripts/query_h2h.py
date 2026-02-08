#!/usr/bin/env python3
import sqlite3
DB='odds_data/odds_history.db'
conn=sqlite3.connect(DB)
cur=conn.cursor()
rows=cur.execute("""
SELECT DISTINCT date(scraped_at) as d, home_team, away_team
FROM live_snapshots
WHERE (home_team='Universidad O&M' AND away_team='Moca') OR (home_team='Moca' AND away_team='Universidad O&M')
ORDER BY d DESC
""").fetchall()
print('Rows found:', len(rows))
for r in rows:
    print(r)
conn.close()