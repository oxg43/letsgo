"""
SQLite database for storing odds snapshots over time.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from odds_tracker.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at TEXT NOT NULL,           -- ISO timestamp
            match_url TEXT,
            country TEXT,
            league TEXT,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            kick_off TEXT,                      -- HH:MM
            odds_1 REAL,
            odds_x REAL,
            odds_2 REAL,
            score TEXT,
            status TEXT                         -- 'upcoming', 'live', 'finished'
        );

        CREATE INDEX IF NOT EXISTS idx_snap_teams
            ON snapshots(home_team, away_team);
        CREATE INDEX IF NOT EXISTS idx_snap_time
            ON snapshots(scraped_at);
        CREATE INDEX IF NOT EXISTS idx_snap_kickoff
            ON snapshots(kick_off);

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            kick_off TEXT,
            signal_type TEXT,                   -- 'STEAM', 'LATE_SHARP', 'VALUE', etc.
            recommended_bet TEXT,               -- '1', 'X', '2'
            confidence REAL,                    -- 0.0 - 1.0
            odds_at_signal REAL,
            reason TEXT,
            acted_on INTEGER DEFAULT 0          -- 0/1
        );

        CREATE TABLE IF NOT EXISTS live_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            minute TEXT,
            score_home INTEGER,
            score_away INTEGER,
            odds_1 REAL,
            odds_x REAL,
            odds_2 REAL,
            country TEXT,
            league TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_live_teams
            ON live_snapshots(home_team, away_team);
        CREATE INDEX IF NOT EXISTS idx_live_time
            ON live_snapshots(scraped_at);

        CREATE TABLE IF NOT EXISTS daily_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            total_matches INTEGER,
            total_signals INTEGER,
            bets_placed INTEGER DEFAULT 0,
            bets_won INTEGER DEFAULT 0,
            bets_lost INTEGER DEFAULT 0,
            profit_loss REAL DEFAULT 0.0,
            notes TEXT
        );
    """)
    conn.commit()
    conn.close()


def save_snapshot(matches: list[dict]):
    """Save a batch of scraped match data."""
    now = datetime.now().isoformat()
    conn = get_connection()
    conn.executemany("""
        INSERT INTO snapshots (scraped_at, match_url, country, league,
            home_team, away_team, kick_off, odds_1, odds_x, odds_2, score, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (now, m.get('url', ''), m.get('country', ''), m.get('league', ''),
         m['home'], m['away'], m.get('kick_off', ''),
         m.get('odds_1'), m.get('odds_x'), m.get('odds_2'),
         m.get('score', ''), m.get('status', 'upcoming'))
        for m in matches
    ])
    conn.commit()
    conn.close()
    return len(matches)


def get_match_history(home: str, away: str) -> list[dict]:
    """Get all snapshots for a specific match, ordered by time."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM snapshots
        WHERE home_team = ? AND away_team = ?
        ORDER BY scraped_at ASC
    """, (home, away)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_upcoming_matches() -> list[dict]:
    """Get latest snapshot for each upcoming match."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.*
        FROM snapshots s
        INNER JOIN (
            SELECT home_team, away_team, MAX(scraped_at) as max_time
            FROM snapshots
            WHERE status IN ('upcoming', '')
            GROUP BY home_team, away_team
        ) latest ON s.home_team = latest.home_team
                 AND s.away_team = latest.away_team
                 AND s.scraped_at = latest.max_time
        ORDER BY s.kick_off ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_snapshot_count() -> int:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    conn.close()
    return count


def get_unique_match_count() -> int:
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(DISTINCT home_team || '|' || away_team) FROM snapshots"
    ).fetchone()[0]
    conn.close()
    return count


def save_signal(signal: dict):
    """Save a generated betting signal."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO signals (generated_at, home_team, away_team, kick_off,
            signal_type, recommended_bet, confidence, odds_at_signal, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        signal['home'], signal['away'], signal.get('kick_off', ''),
        signal['signal_type'], signal['bet'], signal['confidence'],
        signal.get('odds_at_signal', 0), signal.get('reason', '')
    ))
    conn.commit()
    conn.close()


def save_live_snapshot(matches: list[dict]):
    """Save a batch of live match data."""
    now = datetime.now().isoformat()
    conn = get_connection()
    conn.executemany("""
        INSERT INTO live_snapshots (scraped_at, home_team, away_team,
            minute, score_home, score_away, odds_1, odds_x, odds_2, country, league)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (now, m.get('home', ''), m.get('away', ''),
         m.get('minute', ''), m.get('score_home', 0), m.get('score_away', 0),
         m.get('odds_1'), m.get('odds_x'), m.get('odds_2'),
         m.get('country', ''), m.get('league', ''))
        for m in matches
    ])
    conn.commit()
    conn.close()
    return len(matches)


def get_prematch_odds(home: str, away: str) -> dict | None:
    """Get the earliest (opening) odds snapshot for a match."""
    conn = get_connection()
    row = conn.execute("""
        SELECT odds_1, odds_x, odds_2 FROM snapshots
        WHERE home_team = ? AND away_team = ?
        ORDER BY scraped_at ASC LIMIT 1
    """, (home, away)).fetchone()
    conn.close()
    if row:
        return {'odds_1': row['odds_1'], 'odds_x': row['odds_x'], 'odds_2': row['odds_2']}
    return None


def get_prematch_odds_bulk() -> dict:
    """Get opening odds for all matches. Returns {(home, away): {odds_1, odds_x, odds_2}}."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT home_team, away_team, odds_1, odds_x, odds_2
        FROM snapshots s
        INNER JOIN (
            SELECT home_team AS h, away_team AS a, MIN(scraped_at) AS first_time
            FROM snapshots GROUP BY home_team, away_team
        ) first ON s.home_team = first.h AND s.away_team = first.a AND s.scraped_at = first.first_time
    """).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result[(r['home_team'], r['away_team'])] = {
            'odds_1': r['odds_1'], 'odds_x': r['odds_x'], 'odds_2': r['odds_2']
        }
    return result


def get_live_snapshot_count() -> int:
    conn = get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM live_snapshots").fetchone()[0]
    except Exception:
        count = 0
    conn.close()
    return count


def get_all_signals_today() -> list[dict]:
    """Get all signals generated today."""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM signals WHERE generated_at LIKE ? ORDER BY generated_at DESC
    """, (f"{today}%",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
