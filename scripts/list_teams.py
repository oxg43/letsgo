#!/usr/bin/env python3
import sqlite3
DB='odds_data/odds_history.db'
conn=sqlite3.connect(DB)
cur=conn.cursor()
print('Teams containing "Univers":')
for r in cur.execute("SELECT DISTINCT home_team FROM live_snapshots WHERE home_team LIKE '%Univers%';").fetchall():
    print('  ', r[0])
for r in cur.execute("SELECT DISTINCT away_team FROM live_snapshots WHERE away_team LIKE '%Univers%';").fetchall():
    print('  ', r[0])
print('\nTeams containing "Moca":')
for r in cur.execute("SELECT DISTINCT home_team FROM live_snapshots WHERE home_team LIKE '%Moca%';").fetchall():
    print('  ', r[0])
for r in cur.execute("SELECT DISTINCT away_team FROM live_snapshots WHERE away_team LIKE '%Moca%';").fetchall():
    print('  ', r[0])
conn.close()