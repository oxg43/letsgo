#!/usr/bin/env python3
"""
=============================================================================
  DAILY EDGE SCANNER — Automatski skenira upcoming meče i detektira value
  Pokreće se svaki dan u 02:15 putem Windows Task Schedulera

  Strategije:
    S1:    Fav [1.50-2.00] + Drop>=3%      (p=0.002, ROI=+18.6%)
    S2:    SlightFav spread[1-3] + Drop>=5% (p=0.013, ROI=+21.1%)
    S3:    HomeFav + Drop>=10%              (p=0.036, ROI=+15.6%)
    COMBO: S1∩S2 intersection              (p=0.004, ROI=+31.1%)

  Output:  odds_data/signal_map/ONLY_HOME_<date>.tsv
=============================================================================
"""

import os, re, sys, warnings
from pathlib import Path
from datetime import datetime, timedelta, time as dtime
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ─── Paths: integrated into DANAS workspace ─────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "odds_data" / "movement"
SIGNAL_DIR = BASE_DIR / "odds_data" / "signal_map"
SIGNAL_DIR.mkdir(parents=True, exist_ok=True)

NOW   = datetime.now()
STAMP = NOW.strftime("%Y-%m-%d_%H%M")
TODAY = NOW.strftime("%Y-%m-%d")

# ─── ONLY_HOME TSV columns (matches existing signal_map pattern) ─────────
ONLY_HOME_COLUMNS = [
    "captured_at",
    "match_date",
    "kick_off",
    "match",
    "bet",
    "odds",
    "opening",
    "drop_pct",
    "spread",
    "n_snapshots",
    "monitoring_h",
    "strategies",
    "confidence",
    "stake_pct",
    "monotonic",
    "overround",
    "reason",
]


# ═══════════════════════════════════════════════════════════════════════════
#  MATCH DATE ESTIMATION
# ═══════════════════════════════════════════════════════════════════════════
def estimate_match_datetime(last_seen, kick_off_str):
    """Estimate actual match datetime based on last_seen timestamp and kick_off time.
    
    Logic:
    - Parse kick_off as HH:MM
    - Combine with last_seen date
    - If this datetime is in the past (< last_seen), add days until future
    """
    try:
        ko_parts = kick_off_str.split(':')
        ko_hour = int(ko_parts[0])
        ko_minute = int(ko_parts[1]) if len(ko_parts) > 1 else 0
        
        # Create datetime with last_seen date and kick_off time
        match_dt = datetime.combine(last_seen.date(), dtime(ko_hour, ko_minute))
        
        # If this datetime is in the past relative to last_seen, move to next day
        while match_dt < last_seen:
            match_dt += timedelta(days=1)
        
        return match_dt
    except:
        return last_seen


def load_match_calendar():
    """Load per-day match lists from `reports/matches_YYYY-MM-DD.csv`.

    Returns a dataframe with normalized `home`/`away` and `match_date_report` so
    we can look up the real scheduled date when available.
    """
    rep_dir = BASE_DIR / "reports"
    files = sorted(rep_dir.glob("matches_*.csv"))
    if not files:
        return pd.DataFrame(columns=["kick_off", "home_norm", "away_norm", "match_date_report"])

    frames = []
    for fp in files:
        try:
            df = pd.read_csv(fp, dtype=str)
        except Exception:
            continue
        date_str = fp.stem.split("_", 1)[1]
        df = df[[c for c in ["kick_off", "home", "away"] if c in df.columns]]
        df["home_norm"] = df["home"].astype(str).str.strip().str.lower()
        df["away_norm"] = df["away"].astype(str).str.strip().str.lower()
        df["match_date_report"] = date_str
        frames.append(df[["kick_off", "home_norm", "away_norm", "match_date_report"]])

    if not frames:
        return pd.DataFrame(columns=["kick_off", "home_norm", "away_norm", "match_date_report"])

    return pd.concat(frames, ignore_index=True)


# ═══════════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════
def load_data():
    csv_files = sorted(DATA_DIR.glob("*.csv"))
    if not csv_files:
        print("  [!] Nema CSV datoteka u odds_data/movement/")
        sys.exit(1)

    frames = []
    for fp in csv_files:
        try:
            tmp = pd.read_csv(fp, sep=";", dtype=str)
            tmp["source_file"] = fp.name
            frames.append(tmp)
        except Exception as e:
            print(f"  [WARN] Skip {fp.name}: {e}")

    df = pd.concat(frames, ignore_index=True)

    df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    df["kick_off"]   = df["kick_off"].astype(str).str.strip()

    for col in ["odds_1", "odds_x", "odds_2"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["match_key"] = df["kick_off"] + "|" + df["home"] + "|" + df["away"]
    df = df.drop_duplicates(subset=["source_file", "match_key"], keep="first")
    df["status_clean"] = df["status"].str.strip().str.lower()

    return df


# ═══════════════════════════════════════════════════════════════════════════
#  FEATURE ENGINEERING PER MATCH
# ═══════════════════════════════════════════════════════════════════════════
def compute_match_features(df):
    """Izracunaj features za svaki match_key."""

    df_odds = df.dropna(subset=["odds_1", "odds_x", "odds_2"]).copy()
    bad = (df_odds[["odds_1", "odds_x", "odds_2"]] <= 1.0).any(axis=1)
    df_odds = df_odds[~bad].copy()

    if len(df_odds) == 0:
        return pd.DataFrame()

    df_odds = df_odds.sort_values(["match_key", "scraped_at"])

    # Opening / closing
    opening = df_odds.groupby("match_key").first()[
        ["odds_1", "odds_x", "odds_2", "scraped_at"]
    ].rename(columns={
        "odds_1": "open_1", "odds_x": "open_x", "odds_2": "open_2",
        "scraped_at": "first_seen"
    })

    closing = df_odds.groupby("match_key").last()[
        ["odds_1", "odds_x", "odds_2", "scraped_at"]
    ].rename(columns={
        "odds_1": "close_1", "odds_x": "close_x", "odds_2": "close_2",
        "scraped_at": "last_seen"
    })

    n_snaps = df_odds.groupby("match_key").size().rename("n_snapshots")

    # Match info (last row per match) - include match_date if available
    info_cols = ["kick_off", "country", "league", "home", "away", "status_clean", "score"]
    if "match_date" in df_odds.columns:
        info_cols.append("match_date")
    info = df_odds.groupby("match_key").last()[info_cols]

    # Trajectory features
    def traj_features(grp):
        o1 = grp["odds_1"].values
        if len(o1) < 3:
            return pd.Series({
                "vol_1": 0, "monotonic_1": 0,
                "early_drop_1": 0, "late_drop_1": 0
            })
        changes = np.diff(o1)
        vol = np.std(changes)
        total_abs = np.sum(np.abs(changes))
        mono = abs(np.sum(changes)) / total_abs if total_abs > 0 else 1
        mid = len(o1) // 2
        early = (o1[0] - o1[mid]) / o1[0] * 100 if o1[0] > 0 else 0
        late  = (o1[mid] - o1[-1]) / o1[mid] * 100 if o1[mid] > 0 else 0
        return pd.Series({
            "vol_1": vol, "monotonic_1": mono,
            "early_drop_1": early, "late_drop_1": late
        })

    traj = df_odds.groupby("match_key").apply(traj_features)

    # Join
    m = info.join(opening).join(closing).join(n_snaps).join(traj)
    m = m.dropna(subset=["open_1", "close_1"]).copy()

    # Drops
    for side in ["1", "x", "2"]:
        m[f"drop_{side}"] = (
            (m[f"open_{side}"] - m[f"close_{side}"])
            / m[f"open_{side}"] * 100
        )

    # Derived
    m["spread"]           = m["close_2"] - m["close_1"]
    m["monitoring_hours"] = (m["last_seen"] - m["first_seen"]).dt.total_seconds() / 3600
    m["overround"]        = (1/m["close_1"]) + (1/m["close_x"]) + (1/m["close_2"])

    # Kick-off hour
    m["ko_hour"] = pd.to_numeric(m["kick_off"].str.split(":").str[0], errors="coerce")
    
    # Try to resolve exact scheduled date from per-day reports calendar; fall back
    # to time-based heuristic when calendar entry is not found.
    calendar = load_match_calendar()

    def _resolve_match_dt(r):
        ko = str(r["kick_off"]).strip()
        
        # 1. FIRST: Use match_date column directly from CSV if available
        if "match_date" in r.index and pd.notna(r.get("match_date")):
            date_str = str(r["match_date"]).strip()
            if date_str and date_str != "nan":
                try:
                    d = pd.to_datetime(date_str).date()
                    hh, mm = (int(x) for x in ko.split(":")[:2])
                    return datetime.combine(d, dtime(hh, mm))
                except Exception:
                    pass
        
        # 2. FALLBACK: prefer exact match from reports (home+away+kick_off)
        try:
            home = str(r["home"]).strip().lower()
            away = str(r["away"]).strip().lower()
        except Exception:
            return estimate_match_datetime(r["last_seen"], r["kick_off"])

        if not calendar.empty:
            cand = calendar[
                (calendar["home_norm"] == home) &
                (calendar["away_norm"] == away) &
                (calendar["kick_off"] == ko)
            ]
            if len(cand) > 0:
                date_str = cand.iloc[0]["match_date_report"]
                try:
                    d = pd.to_datetime(date_str).date()
                    hh, mm = (int(x) for x in ko.split(":")[:2])
                    return datetime.combine(d, dtime(hh, mm))
                except Exception:
                    pass

        # 3. Last fallback to previous heuristic
        return estimate_match_datetime(r["last_seen"], r["kick_off"])

    m["match_datetime"] = m.apply(_resolve_match_dt, axis=1)
    m["match_date"] = m["match_datetime"].dt.strftime("%Y-%m-%d")

    return m


# ═══════════════════════════════════════════════════════════════════════════
#  STRATEGY DETECTION + SCORING
# ═══════════════════════════════════════════════════════════════════════════
def detect_strategies(row):
    """Vraca dict: strategies hit, confidence score, recommended stake."""
    strategies = []
    score = 0

    close_1 = row["close_1"]
    close_2 = row["close_2"]
    drop_1  = row["drop_1"]
    drop_2  = row["drop_2"]
    drop_x  = row["drop_x"]
    spread  = row["spread"]
    n_snap  = row["n_snapshots"]
    mon_h   = row["monitoring_hours"]
    ko_h    = row["ko_hour"]
    mono    = row.get("monotonic_1", 0)
    vol     = row.get("vol_1", 999)
    late_d  = row.get("late_drop_1", 0)
    early_d = row.get("early_drop_1", 0)

    # ── S1: Favorit [1.50-2.00] + Drop>=3% ──
    s1 = (1.50 <= close_1 <= 2.00) and (drop_1 >= 3)
    if s1:
        strategies.append("S1")
        score += 20

        if 1.60 <= close_1 <= 1.90:  score += 5
        if drop_1 >= 4:              score += 5
        if drop_1 >= 5:              score += 5
        if 12 <= mon_h <= 48:        score += 10
        if 50 <= n_snap <= 200:      score += 10
        if 13 <= ko_h <= 18:         score += 5
        if drop_x < 0:              score += 10
        if drop_2 <= -5:            score += 5
        if mono >= 0.8:             score += 10
        if late_d > early_d:        score += 5

    # ── S2: SlightFav spread[1-3] + Drop>=5% ──
    s2 = (1.0 <= spread <= 3.0) and (drop_1 >= 5)
    if s2:
        strategies.append("S2")
        score += 20

        if 1.5 <= spread <= 2.5:    score += 5
        if drop_2 <= -5:            score += 5
        if mon_h >= 12:             score += 5
        if 13 <= ko_h <= 18:        score += 5

    # ── S3: HomeFav + Drop>=10% ──
    s3 = (close_1 < close_2) and (drop_1 >= 10)
    if s3:
        strategies.append("S3")
        score += 20

        if mon_h >= 12:             score += 5
        if drop_2 <= -5:            score += 5

    # ── COMBO: S1∩S2 ──
    combo = (1.50 <= close_1 <= 2.00) and (1.0 <= spread <= 3.0) and (drop_1 >= 5)
    if combo:
        strategies.append("COMBO")
        score += 15

    # Staking
    if score >= 80:
        stake = 5
    elif score >= 60:
        stake = 3
    elif score >= 40:
        stake = 2
    else:
        stake = 0  # SKIP

    return {
        "strategies":  ",".join(strategies) if strategies else "NONE",
        "confidence":  min(score, 100),
        "stake_pct":   stake,
        "bet":         "HOME WIN (1)" if strategies else "SKIP"
    }


# ═══════════════════════════════════════════════════════════════════════════
#  WRITE ONLY_HOME TSV — into signal_map directory
# ═══════════════════════════════════════════════════════════════════════════
def write_only_home_tsv(actionable_rows):
    """Write ONLY_HOME_{date}.tsv into odds_data/signal_map/."""
    tsv_path = SIGNAL_DIR / f"ONLY_HOME_{TODAY}.tsv"

    rows_to_write = []
    for r in actionable_rows:
        reason_parts = []
        if r["strategies"] != "NONE":
            reason_parts.append(f"Strategies: {r['strategies']}")
        reason_parts.append(f"Drop: {r['drop_1%']:+.1f}%")
        reason_parts.append(f"Spread: {r['spread']:.2f}")
        if r.get("monotonic", 0) >= 0.8:
            reason_parts.append("Clean trend")
        reason_parts.append(f"Mon: {r['monitoring_h']:.0f}h")
        reason_parts.append(f"Snaps: {r['n_snapshots']}")

        rows_to_write.append({
            "captured_at":   NOW.strftime("%H:%M:%S"),
            "match_date":    r.get("match_date", ""),
            "kick_off":      r["kick_off"],
            "match":         f"{r['home']} vs {r['away']}",
            "bet":           r["bet"],
            "odds":          r["close_1"],
            "opening":       r["open_1"],
            "drop_pct":      f"{r['drop_1%']:+.1f}%",
            "spread":        round(r["spread"], 2),
            "n_snapshots":   int(r["n_snapshots"]),
            "monitoring_h":  round(r["monitoring_h"], 1),
            "strategies":    r["strategies"],
            "confidence":    f"{r['confidence']}%",
            "stake_pct":     f"{r['stake_pct']}%",
            "monotonic":     round(r.get("monotonic", 0), 3),
            "overround":     round(r.get("overround", 1), 4),
            "reason":        " | ".join(reason_parts),
        })

    if not rows_to_write:
        print(f"  [!] Nema ONLY_HOME signala za pisanje.")
        return None

    # Write mode - always create fresh file with proper date/time sorting
    with open(tsv_path, "w", encoding="utf-8", newline="") as f:
        import csv
        writer = csv.DictWriter(f, fieldnames=ONLY_HOME_COLUMNS,
                                delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows_to_write:
            writer.writerow(row)

    print(f"  → ONLY_HOME TSV: {tsv_path}  ({len(rows_to_write)} signala)")
    return tsv_path


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN SCANNER
# ═══════════════════════════════════════════════════════════════════════════
def run_scan():
    print("=" * 80)
    print(f"  DAILY EDGE SCANNER — {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. Load
    print("\n  Loading data from odds_data/movement/ ...")
    df = load_data()
    print(f"  Raw rows: {len(df):,}, Unique matches: {df['match_key'].nunique():,}")

    # 2. Compute features
    print("  Computing features...")
    m = compute_match_features(df)
    if len(m) == 0:
        print("  [!] Nema podataka za obradu.")
        return

    # 3. Filter UPCOMING only
    upcoming = m[m["status_clean"] == "upcoming"].copy()
    finished = m[m["status_clean"] == "finished"].copy()

    # remove duplicates where same match marked finished in some snapshot
    finished_keys = set(finished.index)
    upcoming = upcoming[~upcoming.index.isin(finished_keys)]

    # ALSO drop matches whose computed match_datetime is in the past (already started/finished)
    # - some sources still tag status as 'upcoming' even after the match time; this fixes that
    mask_future = upcoming["match_datetime"] > NOW
    dropped_past = upcoming[~mask_future]
    if len(dropped_past) > 0:
        print(f"  [INFO] Dropping {len(dropped_past)} matches with match_datetime <= now (already started/finished)")
    upcoming = upcoming[mask_future]

    print(f"  Upcoming meča: {len(upcoming)}")
    print(f"  Finished meča: {len(finished) + len(dropped_past)}")

    if len(upcoming) == 0:
        print("\n  [!] Nema upcoming meča.")
        return

    # 4. Score each match
    print("  Scoring strategies...")
    results = []
    for mk, row in upcoming.iterrows():
        det = detect_strategies(row)
        results.append({
            "match_key":    mk,
            "match_datetime": row["match_datetime"],
            "match_date":   row["match_date"],
            "kick_off":     row["kick_off"],
            "country":      row["country"],
            "league":       row["league"],
            "home":         row["home"],
            "away":         row["away"],
            "open_1":       round(row["open_1"], 2),
            "close_1":      round(row["close_1"], 2),
            "drop_1%":      round(row["drop_1"], 2),
            "open_2":       round(row["open_2"], 2),
            "close_2":      round(row["close_2"], 2),
            "drop_2%":      round(row["drop_2"], 2),
            "close_x":      round(row["close_x"], 2),
            "drop_x%":      round(row["drop_x"], 2),
            "spread":       round(row["spread"], 2),
            "n_snapshots":  int(row["n_snapshots"]),
            "monitoring_h": round(row["monitoring_hours"], 1),
            "monotonic":    round(row.get("monotonic_1", 0), 3),
            "ko_hour":      row["ko_hour"],
            "overround":    round(row["overround"], 4),
            "strategies":   det["strategies"],
            "confidence":   det["confidence"],
            "stake_pct":    det["stake_pct"],
            "bet":          det["bet"],
        })

    df_out = pd.DataFrame(results)
    # Sort by match_datetime (earliest first), then by confidence
    df_out = df_out.sort_values(["match_datetime", "confidence"], ascending=[True, False])

    # 5. Filter: only actionable (confidence >= 40)
    actionable = df_out[df_out["confidence"] >= 40]
    signals    = df_out[df_out["strategies"] != "NONE"]

    # ── 6. Write ONLY_HOME TSV to signal_map ──
    if len(actionable) > 0:
        action_dicts = actionable.to_dict("records")
        write_only_home_tsv(action_dicts)
    else:
        print(f"\n  [!] Nema actionable ONLY_HOME signala.")

    # 7. Print summary
    print(f"\n{'='*80}")
    print(f"  SCAN RESULTS")
    print(f"{'='*80}")
    print(f"  Total upcoming: {len(upcoming)}")
    print(f"  Signals found:  {len(signals)}")
    print(f"  Actionable:     {len(actionable)} (confidence >= 40)")

    if len(signals) > 0:
        print(f"\n  {'Home':25s} {'Away':25s} {'Odds':>5} {'Drop%':>6} {'Strat':>12} {'Conf':>5} {'Stake':>6}")
        print("  " + "-" * 90)
        for _, r in signals.iterrows():
            stake_str = f"{r['stake_pct']}%" if r['stake_pct'] > 0 else "SKIP"
            print(f"  {r['home']:25s} {r['away']:25s} {r['close_1']:>5.2f} {r['drop_1%']:>+5.1f}% {r['strategies']:>12s} {r['confidence']:>5} {stake_str:>6}")

    if len(actionable) > 0:
        print(f"\n  ★ ACTIONABLE BETS (confidence >= 40):")
        print(f"  {'Home':25s} {'Away':25s} {'League':25s} {'Bet':>15} {'Stake':>6}")
        print("  " + "-" * 100)
        for _, r in actionable.iterrows():
            print(f"  {r['home']:25s} {r['away']:25s} {r['league']:25s} {r['bet']:>15s} {r['stake_pct']:>5}%")
    else:
        print(f"\n  [!] Nema actionable betova danas.")

    # 8. Strategy distribution
    if len(signals) > 0:
        print(f"\n  Strategy distribution:")
        for s in ["S1", "S2", "S3", "COMBO"]:
            cnt = signals["strategies"].str.contains(s).sum()
            if cnt > 0:
                print(f"    {s}: {cnt} meča")

    print(f"\n{'='*80}")
    print(f"  SCAN COMPLETE — {NOW.strftime('%H:%M:%S')}")
    print(f"{'='*80}\n")

    # ── 9. Append to running log ──
    log_path = SIGNAL_DIR / "scanner_log.csv"
    log_row = pd.DataFrame([{
        "scan_time": NOW.strftime("%Y-%m-%d %H:%M"),
        "upcoming": len(upcoming),
        "signals": len(signals),
        "actionable": len(actionable),
        "top_confidence": actionable["confidence"].max() if len(actionable) > 0 else 0,
    }])
    if log_path.exists():
        log_row.to_csv(log_path, mode="a", header=False, index=False)
    else:
        log_row.to_csv(log_path, index=False)


# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    run_scan()
