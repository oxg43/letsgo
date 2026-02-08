"""
Odds Movement Logger — saves every scrape cycle to CSV files 
for later analysis and record-keeping.

Generates:
  - odds_data/movement/YYYY-MM-DD_HHMMSS.csv  (raw snapshot per cycle)
  - odds_data/movement/movement_log.csv         (append-only running log)
  - odds_data/movement/summary_YYYY-MM-DD.csv   (daily summary with changes)
"""
import csv
from datetime import datetime
from pathlib import Path
from odds_tracker.config import DATA_DIR
from odds_tracker.database import get_match_history, get_connection


MOVEMENT_DIR = DATA_DIR / "movement"
MOVEMENT_DIR.mkdir(exist_ok=True)


def save_cycle_snapshot(matches: list[dict], cycle_num: int = 0):
    """
    Save current cycle's scraped data to a timestamped CSV.
    Also appends to the running log.
    """
    now = datetime.now()
    ts = now.strftime('%Y-%m-%d_%H%M%S')

    # ── 1. Individual cycle file ──
    cycle_path = MOVEMENT_DIR / f"{ts}_cycle{cycle_num}.csv"
    fieldnames = [
        'scraped_at', 'kick_off', 'country', 'league',
        'home', 'away', 'odds_1', 'odds_x', 'odds_2',
        'score', 'status'
    ]
    with open(cycle_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        for m in matches:
            writer.writerow({
                'scraped_at': now.isoformat(),
                'kick_off': m.get('kick_off', ''),
                'country': m.get('country', ''),
                'league': m.get('league', ''),
                'home': m.get('home', ''),
                'away': m.get('away', ''),
                'odds_1': m.get('odds_1', ''),
                'odds_x': m.get('odds_x', ''),
                'odds_2': m.get('odds_2', ''),
                'score': m.get('score', ''),
                'status': m.get('status', ''),
            })

    # ── 2. Running log (append) ──
    log_path = MOVEMENT_DIR / "movement_log.csv"
    write_header = not log_path.exists()
    with open(log_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['cycle'] + fieldnames, delimiter=';')
        if write_header:
            writer.writeheader()
        for m in matches:
            writer.writerow({
                'cycle': cycle_num,
                'scraped_at': now.isoformat(),
                'kick_off': m.get('kick_off', ''),
                'country': m.get('country', ''),
                'league': m.get('league', ''),
                'home': m.get('home', ''),
                'away': m.get('away', ''),
                'odds_1': m.get('odds_1', ''),
                'odds_x': m.get('odds_x', ''),
                'odds_2': m.get('odds_2', ''),
                'score': m.get('score', ''),
                'status': m.get('status', ''),
            })

    return cycle_path


def generate_movement_summary():
    """
    Generate a daily summary CSV showing odds movement per match.
    Columns: match, kick_off, first_odds, latest_odds, change%, direction, snapshots
    """
    today = datetime.now().strftime('%Y-%m-%d')
    summary_path = MOVEMENT_DIR / f"summary_{today}.csv"

    conn = get_connection()
    # Get all unique matches
    matches = conn.execute("""
        SELECT DISTINCT home_team, away_team, kick_off
        FROM snapshots
        ORDER BY kick_off
    """).fetchall()

    fieldnames = [
        'kick_off', 'home', 'away', 'status',
        'open_1', 'open_x', 'open_2',
        'latest_1', 'latest_x', 'latest_2',
        'change_1_pct', 'change_x_pct', 'change_2_pct',
        'direction_1', 'direction_x', 'direction_2',
        'snapshots', 'first_seen', 'last_seen'
    ]

    rows = []
    for m in matches:
        home, away, kick_off = m
        history = conn.execute("""
            SELECT * FROM snapshots
            WHERE home_team = ? AND away_team = ?
            ORDER BY scraped_at ASC
        """, (home, away)).fetchall()

        if not history:
            continue

        first = history[0]
        last = history[-1]

        def pct(old, new):
            if old and new and old > 0:
                return round((new - old) / old * 100, 2)
            return 0

        def direction(old, new):
            if not old or not new:
                return '?'
            if new < old - 0.01:
                return 'DOWN'
            elif new > old + 0.01:
                return 'UP'
            return 'STABLE'

        rows.append({
            'kick_off': kick_off or '',
            'home': home,
            'away': away,
            'status': last['status'] if last['status'] else '',
            'open_1': first['odds_1'] if first['odds_1'] else '',
            'open_x': first['odds_x'] if first['odds_x'] else '',
            'open_2': first['odds_2'] if first['odds_2'] else '',
            'latest_1': last['odds_1'] if last['odds_1'] else '',
            'latest_x': last['odds_x'] if last['odds_x'] else '',
            'latest_2': last['odds_2'] if last['odds_2'] else '',
            'change_1_pct': pct(first['odds_1'], last['odds_1']),
            'change_x_pct': pct(first['odds_x'], last['odds_x']),
            'change_2_pct': pct(first['odds_2'], last['odds_2']),
            'direction_1': direction(first['odds_1'], last['odds_1']),
            'direction_x': direction(first['odds_x'], last['odds_x']),
            'direction_2': direction(first['odds_2'], last['odds_2']),
            'snapshots': len(history),
            'first_seen': history[0]['scraped_at'][:19],
            'last_seen': history[-1]['scraped_at'][:19],
        })

    conn.close()

    with open(summary_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        writer.writerows(rows)

    return summary_path, len(rows)
