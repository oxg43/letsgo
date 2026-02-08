#!/usr/bin/env python3
import sqlite3
import sys
from collections import Counter

DB = 'odds_data/odds_history.db'

def normalize_minute(min_text):
    if not min_text:
        return None
    s = str(min_text).strip()
    s = s.replace("'", '')
    s = s.replace("+", '')
    try:
        return int(s)
    except Exception:
        return None


def get_matches_for_pair(conn, a, b, limit=300):
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT DISTINCT date(scraped_at) as d, home_team, away_team
        FROM live_snapshots
        WHERE (home_team LIKE ? AND away_team LIKE ?) OR (home_team LIKE ? AND away_team LIKE ?)
        ORDER BY d DESC
        LIMIT ?
    """, (f'%{a}%', f'%{b}%', f'%{b}%', f'%{a}%', limit)).fetchall()
    return rows


def first_goal_minutes_for_pair(conn, a, b, limit_dates=30):
    rows = get_matches_for_pair(conn, a, b, limit=limit_dates)
    results = []
    for d, home, away in rows:
        cur = conn.cursor()
        snap = cur.execute("""
            SELECT minute, score_home, score_away
            FROM live_snapshots
            WHERE home_team=? AND away_team=? AND date(scraped_at)=?
            ORDER BY scraped_at ASC
        """, (home, away, d)).fetchall()
        # If no snaps, try reverse order
        if not snap:
            snap = cur.execute("""
                SELECT minute, score_home, score_away
                FROM live_snapshots
                WHERE home_team=? AND away_team=? AND date(scraped_at)=?
                ORDER BY scraped_at ASC
            """, (away, home, d)).fetchall()
        first_min = None
        first_scorer = None
        for minute, sh, sa in snap:
            if sh is None: sh = 0
            if sa is None: sa = 0
            total = (sh or 0) + (sa or 0)
            if total > 0:
                m = normalize_minute(minute)
                if m is not None:
                    # decide scorer: compare to previous 0-0 baseline
                    # We don't have event-level scorer data, so infer by comparing to previous row
                    first_min = m
                    # Which team scored first? Find first non-zero difference between this and previous
                    first_scorer = 'unknown'
                    break
        if first_min is not None:
            results.append((d, home, away, first_min))
    return results


def aggregate_distribution(results):
    cnt = Counter()
    for r in results:
        cnt[r[3]] += 1
    return cnt

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: first_goal_analysis.py "Team A" "Team B" [limit_dates=30]')
        sys.exit(1)
    a = sys.argv[1]
    b = sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 30

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    print(f'Analyzing first-goal minutes for {a} vs {b} (last {limit} dates found)')
    res = first_goal_minutes_for_pair(conn, a, b, limit_dates=limit)
    if not res:
        print('No live snapshot matches found for that pair in DB. Proceeding to team-level analysis.')
    else:
        dist = aggregate_distribution(res)
        total = sum(dist.values())
        print(f'Found {total} matches (H2H) with first-goal minute recorded:')
        for minute, count in sorted(dist.items()):
            print(f"  {minute:>3} | {count} ({count/total:.1%})")
        # buckets
        buckets = Counter()
        for _,_,_,m in res:
            b = (m-1)//15
            buckets[b] += 1
        labels = ['1-15','16-30','31-45','46-60','61-75','76-90']
        print('\nH2H Buckets:')
        for i,label in enumerate(labels):
            c = buckets.get(i,0)
            print(f'  {label}: {c} ({c/total:.1%})')

    # Team-level analysis
    def first_goal_for_team(team, limit_matches=30):
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT DISTINCT date(scraped_at) as d, home_team, away_team
            FROM live_snapshots
            WHERE home_team LIKE ? OR away_team LIKE ?
            ORDER BY d DESC LIMIT ?
        """, (f'%{team}%', f'%{team}%', limit_matches)).fetchall()
        res2 = []
        for d, home, away in rows:
            snaps = cur.execute("""
                SELECT minute, score_home, score_away
                FROM live_snapshots
                WHERE home_team=? AND away_team=? AND date(scraped_at)=?
                ORDER BY scraped_at ASC
            """, (home, away, d)).fetchall()
            if not snaps:
                snaps = cur.execute("""
                    SELECT minute, score_home, score_away
                    FROM live_snapshots
                    WHERE home_team=? AND away_team=? AND date(scraped_at)=?
                    ORDER BY scraped_at ASC
                """, (away, home, d)).fetchall()
            first_min = None
            scorer = None
            for minute, sh, sa in snaps:
                if sh is None: sh = 0
                if sa is None: sa = 0
                total = (sh or 0) + (sa or 0)
                if total > 0:
                    m = normalize_minute(minute)
                    if m is not None:
                        first_min = m
                        if (home == team and sh > sa) or (away == team and sa > sh):
                            scorer = 'scored'
                        elif (home == team and sh < sa) or (away == team and sa < sh):
                            scorer = 'conceded'
                        else:
                            scorer = 'unknown'
                        break
            if first_min is not None:
                res2.append((d, home, away, first_min, scorer))
        return res2

    for team in (a, b):
        r = first_goal_for_team(team, limit_matches=limit)
        if not r:
            print(f'No matches found for team {team} in DB (last {limit}).')
            continue
        total = len(r)
        print(f'\nTeam {team} — {total} matches with a recorded first goal:')
        dist = Counter([x[3] for x in r])
        for minute, count in sorted(dist.items()):
            print(f"  {minute:>3} | {count} ({count/total:.1%})")
        buckets = Counter()
        for _,_,_,m,sc in r:
            b = (m-1)//15
            buckets[b] += 1
        print('\nBuckets:')
        for i,label in enumerate(labels):
            c = buckets.get(i,0)
            print(f'  {label}: {c} ({c/total:.1%})')

    conn.close()