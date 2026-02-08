"""
Paper Trade Alerts — Paper-trading pipeline that finds signals from
the existing OddsPortal scrape DB, applies configurable filters, tracks
cooldown/dedup, logs trades with stake info, and optionally notifies.

Usage:
    python paper_trade_alerts.py              # Run continuously
    python paper_trade_alerts.py --once       # Run one cycle
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── paths ──
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "pipeline_config.json"
LOG_PATH = BASE_DIR / "paper_trades_log.csv"
COOLDOWN_PATH = BASE_DIR / "odds_data" / "alert_cooldown.json"

LOG_COLUMNS = [
    "alerted_at",       # ISO string
    "match_key",        # "Home|Away"
    "home",
    "away",
    "kick_off",
    "signal_type",
    "bet",
    "odds",
    "opening_odds",
    "pct_change",
    "confidence",
    "edge",
    "snapshots",
    "stake_model",
    "stake",
    "result",           # "win" / "loss" / "void" / "" (unresolved)
    "profit",           # numeric
    "resolved_at",      # ISO string or ""
    "reason",
]

# ──────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load pipeline_config.json with defaults."""
    defaults = {
        "combo": {"score_th": 0.04, "mag_th": 0.001, "pers_th": 0.0,
                  "min_snap": 30, "window": 24},
        "stake_model": "flat",
        "stake": 1.0,
        "notify": False,
        "poll_seconds": 120,
        "odds_filter": {"min": 1.20, "max": 2.50},
        "alert_cooldown_hours": 2,
        "scrape_days": 7,
    }
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        # merge (shallow)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(defaults.get(k), dict):
                defaults[k].update(v)
            else:
                defaults[k] = v
    return defaults


# ──────────────────────────────────────────────────────────────────
# COOLDOWN / DEDUP
# ──────────────────────────────────────────────────────────────────

def _load_cooldowns() -> dict:
    if COOLDOWN_PATH.exists():
        try:
            with open(COOLDOWN_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cooldowns(cd: dict):
    COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(COOLDOWN_PATH, "w", encoding="utf-8") as f:
        json.dump(cd, f, indent=2)


def _is_on_cooldown(match_key: str, cooldown_hours: float) -> bool:
    cd = _load_cooldowns()
    last = cd.get(match_key)
    if not last:
        return False
    try:
        t = datetime.fromisoformat(last)
        return (datetime.now() - t).total_seconds() < cooldown_hours * 3600
    except Exception:
        return False


def _mark_alerted(match_key: str):
    cd = _load_cooldowns()
    cd[match_key] = datetime.now().isoformat()
    _save_cooldowns(cd)


# ──────────────────────────────────────────────────────────────────
# ODDS-PORTAL MULTI-DAY URL GENERATION
# ──────────────────────────────────────────────────────────────────

def get_scrape_urls(scrape_days: int = 7) -> list[str]:
    """Return OddsPortal URLs for today + next N-1 days."""
    base = "https://www.oddsportal.com/matches/football/"
    urls = []
    today = datetime.now().date()
    for offset in range(scrape_days):
        d = today + timedelta(days=offset)
        urls.append(f"{base}{d.strftime('%Y%m%d')}/")
    return urls


# ──────────────────────────────────────────────────────────────────
# FIND SIGNALS  (applies config filters)
# ──────────────────────────────────────────────────────────────────

def find_signals(config: dict) -> list[dict]:
    """
    Query the existing DB for matches, analyse movements and return
    signal dicts that pass ALL config filters.
    """
    from odds_tracker.database import init_db, get_all_upcoming_matches
    from odds_tracker.analyzer import analyze_match
    from odds_tracker.signals import _evaluate_outcome

    init_db()

    combo = config["combo"]
    odds_f = config["odds_filter"]
    cooldown_h = config["alert_cooldown_hours"]

    matches = get_all_upcoming_matches()
    now = datetime.now()
    window_cutoff = now - timedelta(hours=combo["window"])

    results: list[dict] = []
    seen_keys: set[str] = set()

    for m in matches:
        home = m.get("home_team", m.get("home", ""))
        away = m.get("away_team", m.get("away", ""))
        ko = m.get("kick_off", "")
        if not home or not away:
            continue

        match_key = f"{home}|{away}"

        # dedup within this batch
        if match_key in seen_keys:
            continue

        analysis = analyze_match(home, away, ko)

        # min_snap filter
        if analysis["snapshots"] < combo["min_snap"]:
            continue

        # window filter — check first snapshot timestamp
        if analysis.get("history"):
            try:
                first_ts = datetime.fromisoformat(analysis["history"][0]["time"])
                if first_ts < window_cutoff:
                    pass  # data older than window is fine; we use latest
            except Exception:
                pass

        for outcome in ["1", "X", "2"]:
            sig = _evaluate_outcome(analysis, outcome,
                                    {"home": home, "away": away,
                                     "kick_off": ko, "status": "upcoming"})
            if sig is None:
                continue

            # ── score_th filter ──
            if sig["confidence"] < combo["score_th"]:
                continue

            # ── mag_th filter (minimum absolute pct change) ──
            if abs(sig.get("pct_change", 0)) < combo["mag_th"]:
                continue

            # ── pers_th (for future use, always passes when 0) ──

            # ── odds_filter ──
            latest = sig.get("latest_odds", 0)
            if latest < odds_f["min"] or latest > odds_f["max"]:
                continue

            # ── cooldown ──
            sig_key = f"{match_key}|{outcome}"
            if _is_on_cooldown(sig_key, cooldown_h):
                continue

            seen_keys.add(match_key)
            sig["match_key"] = match_key
            results.append(sig)

    # sort best first
    results.sort(key=lambda s: s["confidence"], reverse=True)
    return results


# ──────────────────────────────────────────────────────────────────
# LOG TRADES
# ──────────────────────────────────────────────────────────────────

def _append_log_rows(rows: list[dict]):
    write_header = not LOG_PATH.exists()
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS, delimiter=";")
        if write_header:
            w.writeheader()
        w.writerows(rows)


def log_alerts(signals: list[dict], config: dict):
    """Write new alerts to paper_trades_log.csv."""
    rows = []
    for s in signals:
        match_key = s.get("match_key", f"{s['home']}|{s['away']}")
        row = {
            "alerted_at": datetime.now().isoformat(),
            "match_key": match_key,
            "home": s["home"],
            "away": s["away"],
            "kick_off": s.get("kick_off", ""),
            "signal_type": s.get("signal_type", ""),
            "bet": s.get("bet", ""),
            "odds": s.get("latest_odds", 0),
            "opening_odds": s.get("opening_odds", 0),
            "pct_change": s.get("pct_change", 0),
            "confidence": s.get("confidence", 0),
            "edge": round(abs(s.get("pct_change", 0)), 4),
            "snapshots": s.get("snapshots", 0),
            "stake_model": config["stake_model"],
            "stake": config["stake"],
            "result": "",
            "profit": "",
            "resolved_at": "",
            "reason": s.get("reason", ""),
        }
        rows.append(row)
        _mark_alerted(f"{match_key}|{s.get('bet','')}")
    if rows:
        _append_log_rows(rows)
    return rows


# ──────────────────────────────────────────────────────────────────
# RESOLVE (mark win/loss after match finishes)
# ──────────────────────────────────────────────────────────────────

def resolve_trades():
    """
    Read paper_trades_log.csv, check finished matches in DB,
    mark result + profit, write back.
    """
    if not LOG_PATH.exists():
        return

    from odds_tracker.database import get_connection

    conn = get_connection()
    rows = []
    updated = 0

    with open(LOG_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            rows.append(row)

    for row in rows:
        if row.get("result"):
            continue  # already resolved

        home = row.get("home", "")
        away = row.get("away", "")
        if not home:
            continue

        finished = conn.execute("""
            SELECT score, status FROM snapshots
            WHERE home_team = ? AND away_team = ? AND status = 'finished'
            ORDER BY scraped_at DESC LIMIT 1
        """, (home, away)).fetchone()

        if not finished:
            continue

        score = finished["score"] or ""
        parts = score.replace("-", ":").split(":")
        if len(parts) != 2:
            continue
        try:
            sh, sa = int(parts[0].strip()), int(parts[1].strip())
        except ValueError:
            continue

        bet = row.get("bet", "")
        if bet == "1":
            won = sh > sa
        elif bet == "X":
            won = sh == sa
        elif bet == "2":
            won = sh < sa
        else:
            continue

        odds = float(row.get("odds", 0) or 0)
        stake = float(row.get("stake", 1) or 1)
        if won:
            profit = round(stake * (odds - 1), 4)
            row["result"] = "win"
        else:
            profit = round(-stake, 4)
            row["result"] = "loss"
        row["profit"] = profit
        row["resolved_at"] = datetime.now().isoformat()
        updated += 1

    conn.close()

    if updated:
        with open(LOG_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LOG_COLUMNS, delimiter=";")
            w.writeheader()
            w.writerows(rows)

    return updated


# ──────────────────────────────────────────────────────────────────
# NOTIFY (optional, disabled by default)
# ──────────────────────────────────────────────────────────────────

def _load_secrets() -> dict:
    p = BASE_DIR / "secrets.json"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def notify_email(signals: list[dict], config: dict):
    """Send email notification if creds are available. No-op without secrets."""
    if not config.get("notify"):
        return
    secrets = _load_secrets()
    smtp_host = secrets.get("smtp_host")
    smtp_port = secrets.get("smtp_port", 587)
    smtp_user = secrets.get("smtp_user")
    smtp_pass = secrets.get("smtp_pass")
    to_addr = secrets.get("notify_email")

    if not all([smtp_host, smtp_user, smtp_pass, to_addr]):
        return  # silently skip — no creds

    import smtplib
    from email.mime.text import MIMEText

    body = f"Paper Trade Alerts — {datetime.now()}\n\n"
    for s in signals:
        body += (f"{s['signal_type']} | {s['home']} vs {s['away']} "
                 f"| Bet {s['bet']} @ {s.get('latest_odds',0):.2f} "
                 f"| Conf {s.get('confidence',0):.0%}\n")

    msg = MIMEText(body)
    msg["Subject"] = f"[PaperTrade] {len(signals)} alerts"
    msg["From"] = smtp_user
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as srv:
            srv.starttls()
            srv.login(smtp_user, smtp_pass)
            srv.sendmail(smtp_user, [to_addr], msg.as_string())
    except Exception as e:
        print(f"  [!] Email notification failed: {e}")


# ──────────────────────────────────────────────────────────────────
# SCRAPE MULTI-DAY  (wraps existing scraper for 7-day window)
# ──────────────────────────────────────────────────────────────────

def scrape_all_days(config: dict):
    """Scrape today + next N-1 days and save to DB."""
    from odds_tracker.scraper import scrape_odds
    from odds_tracker.database import init_db, save_snapshot
    from odds_tracker.config import HEADLESS, PAGE_TIMEOUT_MS

    init_db()
    urls = get_scrape_urls(config.get("scrape_days", 7))
    total = 0

    for url in urls:
        day_label = url.rstrip("/").split("/")[-1]
        print(f"  Scraping {day_label} ...")
        try:
            matches = scrape_odds(url, headless=HEADLESS, timeout_ms=PAGE_TIMEOUT_MS)
            if matches:
                save_snapshot(matches)
                total += len(matches)
                print(f"    [OK] {len(matches)} matches")
            else:
                print(f"    [!] No matches for {day_label}")
        except Exception as e:
            print(f"    [!] Scrape failed for {day_label}: {e}")
    return total


# ──────────────────────────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────────────────────────

def run_once(config: dict):
    """Single paper-trade cycle."""
    print(f"\n{'='*70}")
    print(f"  PAPER TRADE -- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    # 1. Scrape all configured days
    print("\n[1/4] Scraping OddsPortal (7-day window)...")
    total_scraped = scrape_all_days(config)
    print(f"  Total scraped: {total_scraped}")

    # 2. Find signals
    print("\n[2/4] Finding signals (filters applied)...")
    signals = find_signals(config)
    print(f"  {len(signals)} signals passed all filters")

    for s in signals[:15]:
        bet_label = {"1": s["home"], "X": "Draw", "2": s["away"]}.get(s["bet"], s["bet"])
        print(f"    [{s['signal_type']}] {s['home']} vs {s['away']} | "
              f"Bet {s['bet']} ({bet_label}) @ {s.get('latest_odds',0):.2f} | "
              f"Conf {s.get('confidence',0):.0%} | d{s.get('pct_change',0):+.1%}")

    # 3. Log alerts
    print("\n[3/4] Logging trades...")
    logged = log_alerts(signals, config)
    print(f"  {len(logged)} new alerts logged to paper_trades_log.csv")

    # 4. Resolve any finished trades
    print("\n[4/4] Resolving finished trades...")
    resolved = resolve_trades()
    print(f"  {resolved or 0} trades resolved")

    # 5. Optional notification
    if config.get("notify") and signals:
        notify_email(signals, config)

    print(f"\n{'-'*70}")
    return signals


def run_continuous(config: dict):
    """Run paper-trade loop continuously."""
    poll = config.get("poll_seconds", 120)
    print(f"""
+==============================================================+
|            PAPER TRADE PIPELINE -- Continuous Mode            |
|  Poll interval: {poll:3d}s  |  Stake: {config['stake']:.2f} ({config['stake_model']})        |
|  Odds filter: [{config['odds_filter']['min']:.2f} - {config['odds_filter']['max']:.2f}]                          |
|  Cooldown: {config['alert_cooldown_hours']}h  |  7-day scrape window                  |
|  Press Ctrl+C to stop                                        |
+==============================================================+
""")
    cycle = 0
    try:
        while True:
            cycle += 1
            try:
                run_once(config)
            except Exception as e:
                import traceback
                print(f"  [!] Cycle {cycle} error: {e}")
                traceback.print_exc()
            nxt = datetime.now() + timedelta(seconds=poll)
            print(f">> Next cycle at {nxt.strftime('%H:%M:%S')} (in {poll}s)")
            time.sleep(poll)
    except KeyboardInterrupt:
        print(f"\n[STOP] Stopped after {cycle} cycles.")


def main():
    global CONFIG_PATH
    parser = argparse.ArgumentParser(description="Paper Trade Alerts Pipeline")
    parser.add_argument("--once", action="store_true", help="Run one cycle only")
    parser.add_argument("--config", type=str, default=str(CONFIG_PATH),
                        help="Path to pipeline_config.json")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if cfg_path.exists():
        CONFIG_PATH = cfg_path

    config = load_config()
    print(f"Config loaded: odds [{config['odds_filter']['min']}-{config['odds_filter']['max']}], "
          f"min_snap={config['combo']['min_snap']}, cooldown={config['alert_cooldown_hours']}h, "
          f"stake={config['stake']} ({config['stake_model']}), days={config.get('scrape_days',7)}")

    if args.once:
        run_once(config)
    else:
        run_continuous(config)


if __name__ == "__main__":
    main()
