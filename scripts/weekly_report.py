"""
Weekly Report — reads paper_trades_log.csv and outputs a JSON report
with key metrics including bootstrap confidence intervals for profit.

Usage:
    python scripts/weekly_report.py
    python scripts/weekly_report.py --dry-run   # print to stdout only
"""
import argparse
import csv
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOG_PATH = BASE_DIR / "paper_trades_log.csv"
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def read_trades(log_path: Path = LOG_PATH) -> list[dict]:
    """Read paper trades log CSV."""
    if not log_path.exists():
        return []
    with open(log_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        return list(reader)


def bootstrap_ci(values: list[float], n_boot: int = 5000,
                 ci: float = 0.95, seed: int = 42) -> tuple[float, float]:
    """
    Bootstrap confidence interval for the mean of `values`.
    Returns (lower, upper) at the given confidence level.
    """
    if not values:
        return (0.0, 0.0)

    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(values) for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()

    alpha = (1 - ci) / 2
    lo_idx = int(alpha * n_boot)
    hi_idx = int((1 - alpha) * n_boot) - 1
    return (round(means[lo_idx], 4), round(means[hi_idx], 4))


def compute_report(trades: list[dict], last_n_days: int = 7) -> dict:
    """Compute weekly report metrics."""
    now = datetime.now()
    cutoff = now - timedelta(days=last_n_days)

    # filter to resolved trades within window
    resolved = []
    all_alerted = []
    for t in trades:
        alerted_at = t.get("alerted_at", "")
        try:
            alert_dt = datetime.fromisoformat(alerted_at)
        except Exception:
            continue

        if alert_dt >= cutoff:
            all_alerted.append(t)
            if t.get("result") in ("win", "loss"):
                resolved.append(t)

    n_alerts = len(all_alerted)
    n_resolved = len(resolved)

    wins = [t for t in resolved if t["result"] == "win"]
    losses = [t for t in resolved if t["result"] == "loss"]
    accuracy = len(wins) / n_resolved if n_resolved else 0.0

    profits = []
    for t in resolved:
        try:
            profits.append(float(t.get("profit", 0) or 0))
        except (ValueError, TypeError):
            profits.append(0.0)

    cum_profit = round(sum(profits), 4)
    total_staked = sum(float(t.get("stake", 1) or 1) for t in resolved) or 1
    roi = round(cum_profit / total_staked, 4) if total_staked else 0.0

    # bootstrap CI for profit
    ci_lower, ci_upper = bootstrap_ci(profits)

    # last 30 resolved accuracy
    last_30 = resolved[-30:] if len(resolved) >= 30 else resolved
    last_30_wins = [t for t in last_30 if t["result"] == "win"]
    last_30_accuracy = len(last_30_wins) / len(last_30) if last_30 else 0.0

    return {
        "report_date": now.strftime("%Y-%m-%d"),
        "window_days": last_n_days,
        "n_alerts": n_alerts,
        "n_resolved": n_resolved,
        "wins": len(wins),
        "losses": len(losses),
        "accuracy": round(accuracy, 4),
        "cum_profit": cum_profit,
        "ROI": roi,
        "bootstrap_ci_profit_lower": ci_lower,
        "bootstrap_ci_profit_upper": ci_upper,
        "last_30_accuracy": round(last_30_accuracy, 4),
    }


def main():
    parser = argparse.ArgumentParser(description="Weekly Report Generator")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print report to stdout only")
    parser.add_argument("--log", type=str, default=str(LOG_PATH),
                        help="Path to paper_trades_log.csv")
    parser.add_argument("--days", type=int, default=7,
                        help="Window in days (default 7)")
    args = parser.parse_args()

    log_path = Path(args.log)
    trades = read_trades(log_path)
    report = compute_report(trades, last_n_days=args.days)

    report_json = json.dumps(report, indent=2)

    if args.dry_run:
        print(report_json)
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
        out_path = REPORTS_DIR / f"weekly_report_{date_str}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report_json)
        print(f"Report saved to {out_path}")
        print(report_json)

    return report


if __name__ == "__main__":
    main()
