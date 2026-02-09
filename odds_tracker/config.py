"""
Configuration for the Odds Tracker system.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "odds_data"
DB_PATH = DATA_DIR / "odds_history.db"
LOG_DIR = DATA_DIR / "logs"
REPORTS_DIR = DATA_DIR / "reports"


def _today_str():
    """Return today's date as YYYY-MM-DD."""
    return datetime.now().strftime('%Y-%m-%d')


def _today_compact():
    """Return today's date as YYYYMMDD for URLs."""
    return datetime.now().strftime('%Y%m%d')


def _tomorrow_compact():
    """Return tomorrow's date as YYYYMMDD for URLs."""
    return (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')


def get_clean_csv():
    """Return path to today's clean CSV."""
    return BASE_DIR / f"{_today_str()}_clean.csv"


def get_oddsportal_url():
    """Return today's OddsPortal URL (auto-rolls at midnight)."""
    return f"https://www.oddsportal.com/matches/football/{_today_compact()}/"


def get_tomorrow_url():
    """Return tomorrow's OddsPortal URL (auto-rolls at midnight)."""
    return f"https://www.oddsportal.com/matches/football/{_tomorrow_compact()}/"


def get_future_url(days_ahead: int) -> str:
    """Return OddsPortal URL for N days in the future."""
    future = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y%m%d')
    return f"https://www.oddsportal.com/matches/football/{future}/"


def get_future_date(days_ahead: int) -> str:
    """Return date string (YYYY-MM-DD) for N days in the future."""
    return (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')


# Static fallbacks (used by direct import, refreshed at import time)
CLEAN_CSV = get_clean_csv()
ODDSPORTAL_BASE_URL = "https://www.oddsportal.com/matches/football/"
ODDSPORTAL_URL = get_oddsportal_url()
ODDSPORTAL_TOMORROW_URL = get_tomorrow_url()
ODDSPORTAL_LIVE_URL = "https://www.oddsportal.com/inplay-odds/live-now/football/"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)
SCRAPE_INTERVAL_SECONDS = 120  # every 2 minutes
HEADLESS = True  # run browser in headless mode
PAGE_TIMEOUT_MS = 60000

# Analysis intervals (minutes before kick-off)
KEY_INTERVALS = [120, 60, 45, 30, 10, 5]

# Signal thresholds
STEAM_MOVE_THRESHOLD = 0.05       # 5% odds drop = steam move
STRONG_SIGNAL_THRESHOLD = 0.08    # 8% = strong signal
REVERSE_MOVE_THRESHOLD = 0.03     # 3% move against trend
MIN_SNAPSHOTS_FOR_SIGNAL = 3      # need at least 3 data points

# Display
TIMEZONE = "Europe/Zagreb"  # CET
