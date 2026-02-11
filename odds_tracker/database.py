"""
SQLite database for storing odds snapshots over time.
"""
import logging
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from odds_tracker.config import DB_PATH

log = logging.getLogger(__name__)


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

        CREATE TABLE IF NOT EXISTS bet_entry (
            bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            bet TEXT NOT NULL,
            captured_at_utc TEXT NOT NULL,
            kickoff_utc TEXT NOT NULL,
            entry_odds REAL NOT NULL,
            entry_book TEXT,
            close_time_utc TEXT,
            close_odds REAL,
            clv_log REAL,
            result TEXT,
            pnl REAL,
            stake REAL
        );

        CREATE INDEX IF NOT EXISTS idx_bet_match
            ON bet_entry(match_id);
        CREATE INDEX IF NOT EXISTS idx_bet_close
            ON bet_entry(close_odds);
    """)
    # Migration: add match_date column to snapshots if missing
    try:
        conn.execute("ALTER TABLE snapshots ADD COLUMN match_date TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    conn.close()


def save_snapshot(matches: list[dict]):
    """Save a batch of scraped match data."""
    now = datetime.now().isoformat()
    conn = get_connection()
    conn.executemany("""
        INSERT INTO snapshots (scraped_at, match_url, country, league,
            home_team, away_team, kick_off, odds_1, odds_x, odds_2, score, status,
            match_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (now, m.get('url', ''), m.get('country', ''), m.get('league', ''),
         m['home'], m['away'], m.get('kick_off', ''),
         m.get('odds_1'), m.get('odds_x'), m.get('odds_2'),
         m.get('score', ''), m.get('status', 'upcoming'),
         m.get('_match_date', ''))
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


# ═══════════════════════════════════════════════════════════════════
#  BET ENTRY + CLV + SETTLEMENT (TASK 4 & 5)
# ═══════════════════════════════════════════════════════════════════

def insert_bet_entry(match_id: str, home_team: str, away_team: str,
                     bet: str, kickoff_utc: str, entry_odds: float,
                     stake: float = None, entry_book: str = None) -> int | None:
    """Insert a new bet entry. Returns bet_id. Skips duplicates (same match_id+bet)."""
    now_utc = datetime.utcnow().isoformat()
    conn = get_connection()
    # Skip if already recorded for this match+bet
    existing = conn.execute(
        "SELECT bet_id FROM bet_entry WHERE match_id = ? AND bet = ?",
        (match_id, bet)
    ).fetchone()
    if existing:
        conn.close()
        return None
    cur = conn.execute("""
        INSERT INTO bet_entry (match_id, home_team, away_team, bet,
            captured_at_utc, kickoff_utc, entry_odds, entry_book, stake)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (match_id, home_team, away_team, bet,
          now_utc, kickoff_utc, entry_odds, entry_book, stake))
    bet_id = cur.lastrowid
    conn.commit()
    conn.close()
    return bet_id


def settle_closing_for_open_bets(now_utc=None, buffer_minutes=2) -> int:
    """
    For bet_entry rows where close_odds IS NULL and kickoff has passed
    (+ buffer), find the last pre-KO snapshot and record closing odds / CLV.
    Idempotent — safe to call every cycle.
    """
    now_utc = now_utc or datetime.utcnow()
    conn = get_connection()
    open_bets = conn.execute("""
        SELECT * FROM bet_entry
        WHERE close_odds IS NULL AND kickoff_utc != ''
    """).fetchall()

    settled = 0
    for bet in open_bets:
        kickoff = bet['kickoff_utc']
        try:
            ko_dt = datetime.fromisoformat(kickoff)
        except (ValueError, TypeError):
            continue

        # Only settle if now >= kickoff + buffer
        if now_utc < ko_dt + timedelta(minutes=buffer_minutes):
            continue

        home = bet['home_team']
        away = bet['away_team']
        outcome = bet['bet']  # '1', 'X', '2'
        odds_col = {'1': 'odds_1', 'X': 'odds_x', '2': 'odds_2'}.get(outcome)
        if not odds_col:
            continue

        # Last snapshot before kickoff for this match
        row = conn.execute(f"""
            SELECT {odds_col}, scraped_at FROM snapshots
            WHERE home_team = ? AND away_team = ?
            AND scraped_at <= ?
            ORDER BY scraped_at DESC LIMIT 1
        """, (home, away, kickoff)).fetchone()

        if not row or row[odds_col] is None or row[odds_col] <= 1.0:
            # Fallback: average of last 5 snapshots
            fallback = conn.execute(f"""
                SELECT AVG({odds_col}) as avg_odds, MAX(scraped_at) as last_time
                FROM (
                    SELECT {odds_col}, scraped_at FROM snapshots
                    WHERE home_team = ? AND away_team = ?
                    AND {odds_col} IS NOT NULL AND {odds_col} > 1.0
                    ORDER BY scraped_at DESC LIMIT 5
                )
            """, (home, away)).fetchone()
            if not fallback or fallback['avg_odds'] is None:
                log.info("CLV: no snapshots for %s vs %s bet %s — skipping", home, away, outcome)
                continue
            close_odds = fallback['avg_odds']
            close_time = fallback['last_time']
        else:
            close_odds = row[odds_col]
            close_time = row['scraped_at']

        entry_odds = bet['entry_odds']
        clv_log = math.log(entry_odds / close_odds) if close_odds > 0 else None

        conn.execute("""
            UPDATE bet_entry SET close_odds = ?, close_time_utc = ?, clv_log = ?
            WHERE bet_id = ?
        """, (close_odds, close_time, clv_log, bet['bet_id']))
        settled += 1

    conn.commit()
    conn.close()
    if settled:
        log.info("CLV: settled closing odds for %d bet(s)", settled)
    return settled


def settle_results_for_finished_bets() -> int:
    """
    For bet_entry rows where result IS NULL, check if match is finished
    and record W/L/VOID + pnl. Idempotent.
    """
    conn = get_connection()
    unsettled = conn.execute("""
        SELECT * FROM bet_entry WHERE result IS NULL
    """).fetchall()

    settled = 0
    for bet in unsettled:
        home = bet['home_team']
        away = bet['away_team']

        latest = conn.execute("""
            SELECT status, score FROM snapshots
            WHERE home_team = ? AND away_team = ?
            ORDER BY scraped_at DESC LIMIT 1
        """, (home, away)).fetchone()

        if not latest:
            continue

        status = (latest['status'] or '').lower()
        score = latest['score'] or ''

        # Handle postponed / canceled / void
        if status in ('postponed', 'canceled', 'cancelled', 'void', 'abandoned'):
            conn.execute("""
                UPDATE bet_entry SET result = 'VOID', pnl = 0.0 WHERE bet_id = ?
            """, (bet['bet_id'],))
            settled += 1
            continue

        if status != 'finished':
            continue

        # Parse score "H-A" or "H:A"
        try:
            parts = score.replace(':', '-').split('-')
            if len(parts) < 2:
                continue
            home_goals = int(parts[0].strip())
            away_goals = int(parts[1].strip())
        except (ValueError, IndexError):
            continue

        if home_goals > away_goals:
            actual = '1'
        elif home_goals == away_goals:
            actual = 'X'
        else:
            actual = '2'

        bet_outcome = bet['bet']
        stake = bet['stake'] or 0

        if bet_outcome == actual:
            result = 'W'
            pnl = stake * (bet['entry_odds'] - 1) if stake else None
        else:
            result = 'L'
            pnl = -stake if stake else None

        conn.execute("""
            UPDATE bet_entry SET result = ?, pnl = ? WHERE bet_id = ?
        """, (result, pnl, bet['bet_id']))
        settled += 1

    conn.commit()
    conn.close()
    if settled:
        log.info("SETTLE: settled results for %d bet(s)", settled)
    return settled
