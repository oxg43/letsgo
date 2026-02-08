#!/usr/bin/env python3
import sqlite3
DB='odds_data/odds_history.db'
conn=sqlite3.connect(DB)
cur=conn.cursor()
queries=[
    "SELECT COUNT(*) FROM live_snapshots WHERE home_team LIKE '%Universidad O&M%';",
    "SELECT COUNT(*) FROM live_snapshots WHERE away_team LIKE '%Universidad O&M%';",
    "SELECT COUNT(*) FROM live_snapshots WHERE home_team LIKE '%Moca%';",
    "SELECT COUNT(*) FROM live_snapshots WHERE away_team LIKE '%Moca%';",
]
for q in queries:
    print(q, cur.execute(q).fetchone()[0])
conn.close()