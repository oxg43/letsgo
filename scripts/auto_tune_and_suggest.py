"""
Auto-Tune & Suggest — produces candidate combo parameters, validates
them against guardrails (n>=30, accuracy>=0.60, bootstrap CI profit > 0),
and writes a suggestion to suggested_combo.json.

Does NOT auto-apply. Requires manual approval via apply_suggested.sh.

Usage:
    python scripts/auto_tune_and_suggest.py
    python scripts/auto_tune_and_suggest.py --log paper_trades_log.csv
"""
import argparse
import csv
import itertools
import json
import random
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOG_PATH = BASE_DIR / "paper_trades_log.csv"
CONFIG_PATH = BASE_DIR / "pipeline_config.json"
SUGGESTED_PATH = BASE_DIR / "suggested_combo.json"

# Candidate grid for tuning
SCORE_TH_GRID = [0.02, 0.04, 0.06, 0.08]
MAG_TH_GRID = [0.001, 0.005, 0.01, 0.02]
MIN_SNAP_GRID = [10, 20, 30, 50]
WINDOW_GRID = [12, 24, 48]

# Guardrails
MIN_N = 30
MIN_ACCURACY = 0.60


def read_trades(log_path: Path = LOG_PATH) -> list[dict]:
    if not log_path.exists():
        return []
    with open(log_path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def bootstrap_ci_profit(profits: list[float], n_boot: int = 5000,
                        ci: float = 0.95, seed: int = 42) -> tuple[float, float]:
    """Bootstrap CI for cumulative profit."""
    if not profits:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(profits)
    sums = []
    for _ in range(n_boot):
        sample = [rng.choice(profits) for _ in range(n)]
        sums.append(sum(sample))
    sums.sort()
    alpha = (1 - ci) / 2
    lo = sums[int(alpha * n_boot)]
    hi = sums[int((1 - alpha) * n_boot) - 1]
    return (round(lo, 4), round(hi, 4))


def simulate_combo(trades: list[dict], score_th: float, mag_th: float,
                   min_snap: int, window: int) -> dict:
    """
    Simulate a combo by filtering resolved trades as if these
    thresholds were applied.  Returns metrics dict.
    """
    filtered = []
    for t in trades:
        if t.get("result") not in ("win", "loss"):
            continue
        try:
            conf = float(t.get("confidence", 0) or 0)
            pct = abs(float(t.get("pct_change", 0) or 0))
            snaps = int(t.get("snapshots", 0) or 0)
        except (ValueError, TypeError):
            continue

        if conf < score_th:
            continue
        if pct < mag_th:
            continue
        if snaps < min_snap:
            continue
        # window is harder to sim without timestamps — skip for now
        filtered.append(t)

    n = len(filtered)
    if n == 0:
        return {"n": 0, "accuracy": 0, "profit": 0, "ci_lower": 0, "ci_upper": 0}

    wins = sum(1 for t in filtered if t["result"] == "win")
    accuracy = wins / n

    profits = []
    for t in filtered:
        try:
            profits.append(float(t.get("profit", 0) or 0))
        except (ValueError, TypeError):
            profits.append(0.0)

    cum_profit = sum(profits)
    ci_lo, ci_hi = bootstrap_ci_profit(profits)

    return {
        "n": n,
        "accuracy": round(accuracy, 4),
        "profit": round(cum_profit, 4),
        "ci_lower": ci_lo,
        "ci_upper": ci_hi,
    }


def run_tuning(trades: list[dict]) -> list[dict]:
    """Run grid search over combos, return candidates sorted by profit."""
    candidates = []
    for score_th, mag_th, min_snap, window in itertools.product(
        SCORE_TH_GRID, MAG_TH_GRID, MIN_SNAP_GRID, WINDOW_GRID
    ):
        metrics = simulate_combo(trades, score_th, mag_th, min_snap, window)
        combo = {
            "score_th": score_th,
            "mag_th": mag_th,
            "pers_th": 0.0,
            "min_snap": min_snap,
            "window": window,
        }
        candidates.append({
            "combo": combo,
            "metrics": metrics,
        })

    # Sort by profit descending
    candidates.sort(key=lambda c: c["metrics"]["profit"], reverse=True)
    return candidates


def select_best(candidates: list[dict]) -> dict | None:
    """
    Select the best combo only if it passes ALL guardrails:
      - n >= MIN_N
      - accuracy >= MIN_ACCURACY
      - bootstrap lower CI of profit > 0
    """
    for c in candidates:
        m = c["metrics"]
        if m["n"] >= MIN_N and m["accuracy"] >= MIN_ACCURACY and m["ci_lower"] > 0:
            return c
    return None


def main():
    parser = argparse.ArgumentParser(description="Auto-Tune & Suggest")
    parser.add_argument("--log", type=str, default=str(LOG_PATH))
    args = parser.parse_args()

    log_path = Path(args.log)
    trades = read_trades(log_path)
    print(f"Loaded {len(trades)} trades from {log_path}")

    resolved = [t for t in trades if t.get("result") in ("win", "loss")]
    print(f"  Resolved: {len(resolved)}")

    if not resolved:
        print("  ⚠ No resolved trades — cannot tune. Collect more data first.")
        # Write empty suggestion
        suggestion = {
            "status": "insufficient_data",
            "timestamp": datetime.now().isoformat(),
            "reason": f"Only {len(resolved)} resolved trades (need {MIN_N}+)",
            "suggested_combo": None,
        }
        with open(SUGGESTED_PATH, "w", encoding="utf-8") as f:
            json.dump(suggestion, f, indent=2)
        print(f"  Wrote {SUGGESTED_PATH}")
        return

    candidates = run_tuning(resolved)
    print(f"\n  Evaluated {len(candidates)} combos")

    # Show top 5
    print("\n  Top 5 combos:")
    for i, c in enumerate(candidates[:5]):
        m = c["metrics"]
        cm = c["combo"]
        flag = "✓" if (m["n"] >= MIN_N and m["accuracy"] >= MIN_ACCURACY
                       and m["ci_lower"] > 0) else "✗"
        print(f"    {flag} #{i+1}: score≥{cm['score_th']}, mag≥{cm['mag_th']}, "
              f"snap≥{cm['min_snap']}, win={cm['window']}h | "
              f"n={m['n']}, acc={m['accuracy']:.0%}, "
              f"profit={m['profit']:.2f}, CI=[{m['ci_lower']:.2f}, {m['ci_upper']:.2f}]")

    best = select_best(candidates)

    if best:
        suggestion = {
            "status": "suggested",
            "timestamp": datetime.now().isoformat(),
            "suggested_combo": best["combo"],
            "metrics": best["metrics"],
            "guardrails": {
                "min_n": MIN_N,
                "min_accuracy": MIN_ACCURACY,
                "ci_lower_positive": True,
            },
            "reason": (f"Passed all guardrails: n={best['metrics']['n']}, "
                       f"acc={best['metrics']['accuracy']:.0%}, "
                       f"CI_lower={best['metrics']['ci_lower']:.2f}"),
        }
        print(f"\n  ✅ SUGGESTED COMBO: {json.dumps(best['combo'])}")
        print(f"     Metrics: {json.dumps(best['metrics'])}")
        print(f"\n  ⚠  NOT auto-applied. Run apply_suggested.sh to apply.")
    else:
        suggestion = {
            "status": "no_candidate",
            "timestamp": datetime.now().isoformat(),
            "reason": f"No combo passed guardrails (n>={MIN_N}, acc>={MIN_ACCURACY:.0%}, CI>0)",
            "suggested_combo": None,
            "top_candidate": candidates[0] if candidates else None,
        }
        print(f"\n  ❌ No combo passed all guardrails.")
        if candidates:
            top = candidates[0]
            print(f"     Best: n={top['metrics']['n']}, acc={top['metrics']['accuracy']:.0%}, "
                  f"ci_lo={top['metrics']['ci_lower']:.2f}")

    with open(SUGGESTED_PATH, "w", encoding="utf-8") as f:
        json.dump(suggestion, f, indent=2)
    print(f"\n  Wrote {SUGGESTED_PATH}")


if __name__ == "__main__":
    main()
