#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  R7 PAPER TRADING SYSTEM
  
  Strategija: R7 — Quality≥70, Odds 1.80-4.00, Non-exotic, Rising Type OK
  Backtested: N=136, Acc=44.9%, ROI=+28.2%, MaxDD=24.9%, p=0.0034
  
  CLI:
    python trader.py --scan              Prikaži trenutne R7 signale
    python trader.py --report            Dnevni izvještaj
    python trader.py --update            Updejta rezultate završenih mečeva
    python trader.py --weekly            Tjedni validacijski report
    python trader.py --watch             Kontinuirano praćenje (svake 3 min)
═══════════════════════════════════════════════════════════════════════════════
"""

import argparse
import csv
import json
import os
import shutil
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, date as dateclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

warnings.filterwarnings("ignore")

# ─── Fix Windows console encoding ───────────────────────────────────────
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── Paths ───────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "odds_data" / "movement"
OUTPUT_DIR = BASE_DIR / "output"
ALERTS_DIR = OUTPUT_DIR / "alerts"
TRADES_DIR = OUTPUT_DIR / "paper_trades"
LOGS_DIR   = OUTPUT_DIR / "logs"
REPORTS_DIR = OUTPUT_DIR / "reports"

for d in [ALERTS_DIR, TRADES_DIR, LOGS_DIR, REPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

NOW   = datetime.now()
TODAY = NOW.strftime("%Y-%m-%d")
STAMP = NOW.strftime("%Y-%m-%d_%H%M")

# ─── Paper Trade CSV ─────────────────────────────────────────────────────
TRADES_FILE = TRADES_DIR / "trades.csv"
TRADES_FIELDS = [
    "trade_id", "date", "match_id", "home", "away", "league", "country",
    "predicted_outcome", "score_composite", "signal_type", "odds_at_signal",
    "stake_pct", "stake_eur", "status", "actual_result", "actual_score",
    "profit_loss", "cumulative_pnl", "account", "notes",
]

# ─── Bankroll ────────────────────────────────────────────────────────────
BANKROLL_EUR = 1000  # Paper bankroll

# ─── Stop/Pause Rules ───────────────────────────────────────────────────
STOP_RULES = {
    'max_drawdown_pct': 25,        # STOP ako DD > 25%
    'pause_drawdown_pct': 15,      # PAUZA ako DD 15-25%
    'min_accuracy_50bets': 35,     # STOP ako acc < 35% na zadnjih 50
    'max_consecutive_losses': 8,   # PAUZA ako 8 gubitaka zaredom
    'negative_weeks': 3,           # STOP ako 3 tjedna zaredom negativan
    'min_bets_for_eval': 50,       # Ne evaluiraj prije 50 oklada
}

# ─── Exotic leagues (R7 ih ISKLJUČUJE) ──────────────────────────────────
EXOTIC_COUNTRIES = {
    "Aruba", "Barbados", "Bermuda", "Suriname", "Gibraltar", "Andorra",
    "San Marino", "Faroe Islands", "Liechtenstein", "Malta", "Luxembourg",
    "Nicaragua", "El Salvador", "Honduras", "Guatemala", "Panama",
    "Dominican Republic", "Trinidad and Tobago", "Jamaica", "Guam",
    "Tahiti", "New Caledonia", "Fiji", "Samoa", "Tonga", "Vanuatu",
}

# R7 prihvaća samo ove rising tipove
R7_ACCEPTED_TYPES = {"DRIFT_UP", "CORRECTION_UP", "NEGLECT_UP"}


# ═══════════════════════════════════════════════════════════════════════════
#  DATA LOADING — Identičan format kao daily_scanner
# ═══════════════════════════════════════════════════════════════════════════
def load_movement_data(days_back=2):
    """Load cycle CSVs from last N days. Returns DataFrame."""
    csv_files = []
    for d in range(days_back, -1, -1):
        day = (NOW - timedelta(days=d)).strftime("%Y-%m-%d")
        csv_files.extend(sorted(DATA_DIR.glob(f"{day}*.csv")))

    if not csv_files:
        print("  [!] Nema CSV datoteka u odds_data/movement/")
        return pd.DataFrame()

    frames = []
    for fp in csv_files:
        try:
            tmp = pd.read_csv(fp, sep=";", dtype=str)
            tmp["source_file"] = fp.name
            frames.append(tmp)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    df["kick_off"] = df["kick_off"].astype(str).str.strip()

    for col in ["odds_1", "odds_x", "odds_2"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["match_id"] = df["home"].astype(str) + "|" + df["away"].astype(str)
    df = df.drop_duplicates(subset=["source_file", "match_id"], keep="first")

    return df


def compute_features(df):
    """Compute per-match features from all snapshots."""
    df_valid = df.dropna(subset=["odds_1", "odds_x", "odds_2"]).copy()
    bad = (df_valid[["odds_1", "odds_x", "odds_2"]] <= 1.0).any(axis=1)
    df_valid = df_valid[~bad].copy()

    if len(df_valid) == 0:
        return pd.DataFrame()

    df_valid = df_valid.sort_values(["match_id", "scraped_at"])

    # Opening / closing
    opening = df_valid.groupby("match_id").first()[
        ["odds_1", "odds_x", "odds_2", "scraped_at"]
    ].rename(columns={
        "odds_1": "open_1", "odds_x": "open_x", "odds_2": "open_2",
        "scraped_at": "first_seen",
    })

    closing = df_valid.groupby("match_id").last()[
        ["odds_1", "odds_x", "odds_2", "scraped_at"]
    ].rename(columns={
        "odds_1": "close_1", "odds_x": "close_x", "odds_2": "close_2",
        "scraped_at": "last_seen",
    })

    n_snaps = df_valid.groupby("match_id").size().rename("n_snapshots")

    # Match info from last row per match
    info_cols = ["kick_off", "country", "league", "home", "away", "status", "score", "match_date"]
    info_cols = [c for c in info_cols if c in df_valid.columns]
    info = df_valid.groupby("match_id").last()[info_cols]

    # Trajectory features per market (extended for R7)
    def traj_features(grp):
        result = {}
        for market, col in [("1", "odds_1"), ("X", "odds_x"), ("2", "odds_2")]:
            side = market.replace("X", "x")
            vals = grp[col].values
            if len(vals) < 3:
                result[f"vol_{side}"] = 0
                result[f"mono_{side}"] = 0
                result[f"smooth_rise_{side}"] = False
                result[f"n_changes_{side}"] = 0
                result[f"n_direction_changes_{side}"] = 0
                result[f"max_single_move_{side}"] = 0.0
                result[f"late_conc_{side}"] = 0.5
                result[f"rise_start_phase_{side}"] = "unknown"
                continue
            changes = np.diff(vals)
            total_abs = np.sum(np.abs(changes))
            mono = abs(np.sum(changes)) / total_abs if total_abs > 0 else 1
            result[f"vol_{side}"] = np.std(changes)
            result[f"mono_{side}"] = mono
            pct_change = (vals[-1] - vals[0]) / vals[0] * 100 if vals[0] > 0 else 0
            result[f"smooth_rise_{side}"] = (mono >= 0.7 and pct_change < -3)

            # R7 trajectory features
            tf = compute_trajectory_features(vals)
            result[f"n_changes_{side}"] = tf["n_changes"]
            result[f"n_direction_changes_{side}"] = tf["n_direction_changes"]
            result[f"max_single_move_{side}"] = tf["max_single_move_pct"]
            result[f"late_conc_{side}"] = tf["late_conc"]
            result[f"rise_start_phase_{side}"] = tf["rise_start_phase"]
        return pd.Series(result)

    traj = df_valid.groupby("match_id").apply(traj_features)

    m = info.join(opening).join(closing).join(n_snaps).join(traj)
    m = m.dropna(subset=["open_1", "close_1"]).copy()

    # Percentage changes
    for side in ["1", "x", "2"]:
        o = f"open_{side}"
        c = f"close_{side}"
        m[f"pct_change_{side}"] = (m[c] - m[o]) / m[o] * 100

    # Alias for R7 (backtest uses change_pct_ prefix)
    for side in ["1", "x", "2"]:
        m[f"change_pct_{side}"] = m[f"pct_change_{side}"]

    # Overround
    m["open_overround"] = (1 / m["open_1"]) + (1 / m["open_x"]) + (1 / m["open_2"])
    m["close_overround"] = (1 / m["close_1"]) + (1 / m["close_x"]) + (1 / m["close_2"])
    m["overround_change"] = (m["close_overround"] - m["open_overround"]) * 100  # percentage points

    # Monitoring hours
    m["mon_hours"] = (m["last_seen"] - m["first_seen"]).dt.total_seconds() / 3600

    # Status
    m["status_clean"] = m["status"].astype(str).str.strip().str.lower()

    return m


# ═══════════════════════════════════════════════════════════════════════════
#  TRAJECTORY FEATURES — helper for R7
# ═══════════════════════════════════════════════════════════════════════════
def compute_trajectory_features(vals):
    """
    Compute trajectory features from a time-series of odds values for one market.
    
    Args:
        vals: numpy array of odds values sorted by time
        
    Returns:
        dict with: n_changes, n_direction_changes, max_single_move_pct,
                   late_conc, rise_start_phase
    """
    if len(vals) < 2:
        return {
            "n_changes": 0,
            "n_direction_changes": 0,
            "max_single_move_pct": 0.0,
            "late_conc": 0.5,
            "rise_start_phase": "unknown",
        }
    
    changes = np.diff(vals)
    
    # n_changes: snapshot-ovi u kojima se kvota stvarno promijenila
    n_changes = int(np.sum(changes != 0))
    
    # n_direction_changes: koliko puta se smjer okrenuo (rast→pad ili pad→rast)
    nonzero = changes[changes != 0]
    if len(nonzero) > 1:
        signs = np.sign(nonzero)
        n_direction_changes = int(np.sum(signs[1:] != signs[:-1]))
    else:
        n_direction_changes = 0
    
    # max_single_move_pct: najveća apsolutna promjena kao % od opening odds
    open_val = vals[0]
    if open_val > 0:
        max_single_move_pct = float(np.max(np.abs(changes)) / open_val * 100)
    else:
        max_single_move_pct = 0.0
    
    # late_conc: udio ukupne apsolutne promjene u zadnjih 30% snapshota
    total_abs_change = np.sum(np.abs(changes))
    if total_abs_change > 0:
        cutoff = int(len(changes) * 0.7)
        late_change = np.sum(np.abs(changes[cutoff:]))
        late_conc = float(late_change / total_abs_change)
    else:
        late_conc = 0.5
    
    # rise_start_phase: "early" ako >50% promjene u prvoj polovici
    if total_abs_change > 0:
        midpoint = len(changes) // 2
        first_half_change = np.sum(np.abs(changes[:midpoint]))
        rise_start_phase = "early" if first_half_change > 0.5 * total_abs_change else "late"
    else:
        rise_start_phase = "unknown"
    
    return {
        "n_changes": n_changes,
        "n_direction_changes": n_direction_changes,
        "max_single_move_pct": max_single_move_pct,
        "late_conc": late_conc,
        "rise_start_phase": rise_start_phase,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  R7 RISING TYPE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════
def classify_rising_type(row, suffix):
    """
    Klasificira tip rastućeg kretanja kvote.
    
    Args:
        row: dict/Series s feature-ima utakmice
        suffix: "1", "x", ili "2" (home, draw, away market)
    
    Returns:
        string: NOT_RISING, LATE_SPIKE_UP, STEAM_UP, OVERREACTION_UP,
                BOUNCE_UP, NEGLECT_UP, CORRECTION_UP, DRIFT_UP
    
    R7 prihvaća SAMO: DRIFT_UP, CORRECTION_UP, NEGLECT_UP
    """
    cp   = row.get(f"change_pct_{suffix}", 0) or 0
    nc   = row.get(f"n_changes_{suffix}", 0) or 0
    ndc  = row.get(f"n_direction_changes_{suffix}", 0) or 0
    msm  = row.get(f"max_single_move_{suffix}", 0) or 0
    lc   = row.get(f"late_conc_{suffix}", 0.5)
    ns   = row.get("n_snapshots", 0) or 0

    # Mora biti barem 0.5% rising
    if cp <= 0.5:
        return "NOT_RISING"

    dc_ratio = ndc / nc if nc > 0 else 0

    # LATE_SPIKE_UP — >80% promjene u zadnjih 30% snapshota, >3% ukupno
    if lc > 0.80 and cp > 3:
        return "LATE_SPIKE_UP"

    # STEAM_UP — jedna velika promjena (>60% ukupne), >3% ukupno
    if msm > 0 and cp > 0 and (msm / cp) > 0.60 and cp > 3:
        return "STEAM_UP"

    # OVERREACTION_UP — >15% ukupna promjena
    if cp > 15:
        return "OVERREACTION_UP"

    # BOUNCE_UP — puno promjena smjera (>40% direction changes)
    if dc_ratio > 0.40 and cp > 1:
        return "BOUNCE_UP"

    # NEGLECT_UP — kvota raste, malo stvarnih promjena (<15% snapshota)
    changes_ratio = nc / ns if ns > 0 else 1
    if changes_ratio < 0.15 and cp > 1:
        return "NEGLECT_UP"

    # CORRECTION_UP — rani rast, niska kasna koncentracija
    rsp = row.get(f"rise_start_phase_{suffix}", "unknown")
    if rsp == "early" and lc < 0.3 and dc_ratio < 0.25:
        return "CORRECTION_UP"

    # DRIFT_UP — default glatki rast
    if dc_ratio < 0.25:
        return "DRIFT_UP"

    return "DRIFT_UP"


# ═══════════════════════════════════════════════════════════════════════════
#  R7 QUALITY SCORE
# ═══════════════════════════════════════════════════════════════════════════
def compute_quality_score(row, suffix, league_roi_map, med_speed, med_counter):
    """
    0–100 quality score za RISING outcome.
    R7 filtira quality >= 70.
    
    BONUSI (max 100):
      +20  smoothness > 0.7
      +15  speed < med_speed
      +15  |overround_change| < 1.0
      +15  counter < med_counter
      +10  late_conc < 0.5
      +10  close_odds >= 2.0
      +10  league ROI > 0
      +5   1.80 <= odds <= 4.00
    
    PENALI:
      -20  smoothness < 0.3
      -20  |overround_change| > 3.0
      -15  late_conc > 0.80
    """
    score = 0
    cp   = abs(row.get(f"change_pct_{suffix}", 0) or 0)
    nc   = max(row.get(f"n_changes_{suffix}", 0) or 0, 1)
    ndc  = row.get(f"n_direction_changes_{suffix}", 0) or 0
    ns   = max(row.get("n_snapshots", 1) or 1, 1)
    lc   = row.get(f"late_conc_{suffix}", 0.5)
    orc  = abs(row.get("overround_change", 0) or 0)
    close_odds = row.get(f"close_{suffix}", 3.0) or 3.0

    smoothness = 1 - (ndc / nc)
    speed      = cp / max(ns, 1)

    # Kontra-kretanje: koliko se suprotni market promijenio
    opposites = {"1": "2", "2": "1", "x": "1"}
    opp = opposites.get(suffix, "2")
    counter = abs(row.get(f"change_pct_{opp}", 0) or 0)

    is_fav = close_odds < 2.0

    # ═══ BONUSI ═══
    if smoothness > 0.7:     score += 20
    if speed < med_speed:    score += 15
    if orc < 1.0:            score += 15
    if counter < med_counter: score += 15
    if lc < 0.5:             score += 10
    if not is_fav:           score += 10
    lg = row.get("league", "")
    if league_roi_map.get(lg, -1) > 0: score += 10
    try:
        co = float(close_odds)
        if 1.80 <= co <= 4.00: score += 5
    except Exception:
        pass

    # ═══ PENALI ═══
    if smoothness < 0.3:     score -= 20
    if orc > 3.0:            score -= 20
    if lc > 0.80:            score -= 15

    return max(0, min(100, score))


# ═══════════════════════════════════════════════════════════════════════════
#  REFERENCE MEDIANS (for quality scoring)
# ═══════════════════════════════════════════════════════════════════════════
def compute_reference_medians(m):
    """
    Compute median speed and median counter-movement from finished matches.
    These are reference values for quality scoring.
    """
    finished = m[m["status_clean"] == "finished"]
    
    # med_speed: medijan brzine (change_pct / n_snapshots) za rising outcomes
    speeds = []
    for suffix in ("1", "x", "2"):
        cp_col = f"change_pct_{suffix}"
        if cp_col not in finished.columns:
            continue
        rising = finished[finished[cp_col] > 0.5]
        if len(rising) == 0:
            continue
        ns_vals = rising["n_snapshots"].replace(0, 1).fillna(1)
        cp_vals = rising[cp_col].fillna(0).abs()
        speeds.extend((cp_vals / ns_vals).tolist())
    med_speed = float(np.median(speeds)) if speeds else 0.5
    
    # med_counter: medijan kontra-kretanja za rising outcomes
    counter_vals = []
    opp_map = {"1": "2", "x": "1", "2": "1"}
    for suffix in ("1", "x", "2"):
        cp_col = f"change_pct_{suffix}"
        if cp_col not in finished.columns:
            continue
        rising = finished[finished[cp_col] > 0.5]
        if len(rising) == 0:
            continue
        opp = opp_map[suffix]
        opp_col = f"change_pct_{opp}"
        if opp_col in rising.columns:
            counter_vals.extend(rising[opp_col].fillna(0).abs().tolist())
    med_counter = float(np.median(counter_vals)) if counter_vals else 3.0
    
    return med_speed, med_counter


# ═══════════════════════════════════════════════════════════════════════════
#  GHOST MATCH FILTERING
# ═══════════════════════════════════════════════════════════════════════════
def filter_ghost_matches(df, upcoming_df):
    """
    Remove upcoming matches whose team pair has ever been seen as 'finished'.
    """
    finished_mask = df["status"].astype(str).str.strip().str.lower() == "finished"
    finished_pairs = set()
    for _, row in df[finished_mask].iterrows():
        pair = f"{str(row['home']).strip()}|{str(row['away']).strip()}"
        finished_pairs.add(pair)
    
    upcoming_df = upcoming_df.copy()
    upcoming_df["_team_pair"] = (
        upcoming_df["home"].astype(str).str.strip() + "|" +
        upcoming_df["away"].astype(str).str.strip()
    )
    before = len(upcoming_df)
    upcoming_df = upcoming_df[~upcoming_df["_team_pair"].isin(finished_pairs)]
    after = len(upcoming_df)
    
    if before != after:
        print(f"  Ghost filter: {before} → {after} (removed {before - after} ghost matches)")
    
    return upcoming_df


# ═══════════════════════════════════════════════════════════════════════════
#  KELLY CRITERION
# ═══════════════════════════════════════════════════════════════════════════
def kelly_stake(odds, win_prob, fraction=0.25):
    """Kelly criterion: f* = (bp - q) / b, then apply fraction."""
    b = odds - 1
    p = win_prob
    q = 1 - p
    if b <= 0:
        return 0
    f = (b * p - q) / b
    return max(0, f * fraction)


# ═══════════════════════════════════════════════════════════════════════════
#  R7 SCANNER (--scan)
# ═══════════════════════════════════════════════════════════════════════════
def run_scan(threshold=70, quiet=False):
    """
    R7 Scanner: scan upcoming matches, apply R7 filter, display signals.
    
    R7 filter (ALL 4 conditions must pass):
      1. quality >= 70
      2. 1.80 <= close_odds <= 4.00
      3. country NOT IN EXOTIC_COUNTRIES
      4. rising_type IN (DRIFT_UP, CORRECTION_UP, NEGLECT_UP)
    """
    if not quiet:
        print("═" * 70)
        print(f"  R7 SCANNER — {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Filter: quality≥{threshold}, odds 1.80-4.00, non-exotic, rising type OK")
        print("═" * 70)
    
    # Load data
    df = load_movement_data(days_back=2)
    if df.empty:
        print("  [!] Nema podataka.")
        return []
    
    if not quiet:
        print(f"  Loaded {len(df):,} rows from {df['source_file'].nunique()} files")
    
    # Compute features (including new trajectory features)
    m = compute_features(df)
    if m.empty:
        print("  [!] Nema podataka za obradu.")
        return []
    
    # Compute reference medians from finished matches
    med_speed, med_counter = compute_reference_medians(m)
    if not quiet:
        print(f"  Reference: med_speed={med_speed:.3f}, med_counter={med_counter:.3f}")
    
    # Filter to upcoming only
    upcoming = m[m["status_clean"] == "upcoming"].copy()
    if not quiet:
        print(f"  Upcoming matches: {len(upcoming)}")
    
    if len(upcoming) == 0:
        return []
    
    # Ghost match filtering
    upcoming = filter_ghost_matches(df, upcoming)
    if not quiet:
        print(f"  After ghost filter: {len(upcoming)}")
    
    if len(upcoming) == 0:
        return []
    
    # League ROI map (prazan za početak)
    league_roi_map = {}
    
    # Score each match × each market with R7 filter
    all_signals = []
    for match_id, row in upcoming.iterrows():
        country = str(row.get("country", "")).strip()
        
        # R7 condition 3: non-exotic league
        if country in EXOTIC_COUNTRIES:
            continue
        
        for suffix in ["1", "x", "2"]:
            # Compute change_pct for this market
            cp = row.get(f"change_pct_{suffix}", 0)
            if pd.isna(cp):
                cp = 0
            
            # Must be rising (change_pct > 0.5%)
            if cp <= 0.5:
                continue
            
            close_odds = row.get(f"close_{suffix}", 0)
            if pd.isna(close_odds) or close_odds <= 1.01:
                continue
            
            # R7 condition 2: odds 1.80-4.00
            if not (1.80 <= close_odds <= 4.00):
                continue
            
            # R7 condition 4: rising type must be acceptable
            rtype = classify_rising_type(row, suffix)
            if rtype not in R7_ACCEPTED_TYPES:
                continue
            
            # R7 condition 1: quality >= threshold (default 70)
            quality = compute_quality_score(row, suffix, league_roi_map, med_speed, med_counter)
            if quality < threshold:
                continue
            
            # Build quality breakdown string
            nc = max(row.get(f"n_changes_{suffix}", 0) or 0, 1)
            ndc = row.get(f"n_direction_changes_{suffix}", 0) or 0
            smoothness = 1 - (ndc / nc)
            speed = abs(cp) / max(row.get("n_snapshots", 1), 1)
            orc = abs(row.get("overround_change", 0) or 0)
            opp_map = {"1": "2", "2": "1", "x": "1"}
            counter = abs(row.get(f"change_pct_{opp_map.get(suffix, '2')}", 0) or 0)
            lc = row.get(f"late_conc_{suffix}", 0.5)
            
            breakdown = []
            if smoothness > 0.7:     breakdown.append("smooth(+20)")
            if speed < med_speed:    breakdown.append("slow(+15)")
            if orc < 1.0:            breakdown.append("orc_ok(+15)")
            if counter < med_counter: breakdown.append("weak_ctr(+15)")
            if lc < 0.5:             breakdown.append("not_late(+10)")
            if close_odds >= 2.0:    breakdown.append("not_fav(+10)")
            lg = row.get("league", "")
            if league_roi_map.get(lg, -1) > 0: breakdown.append("league_roi(+10)")
            if 1.80 <= close_odds <= 4.00: breakdown.append("sweet(+5)")
            if smoothness < 0.3:     breakdown.append("rough(-20)")
            if orc > 3.0:            breakdown.append("orc_bad(-20)")
            if lc > 0.80:            breakdown.append("late(-15)")
            
            # Predicted outcome
            if suffix == "1":
                predicted = "HOME"
            elif suffix == "x":
                predicted = "DRAW"
            else:
                predicted = "AWAY"
            
            # Win probability from R7 backtest
            est_winprob = 0.449
            kelly_pct = kelly_stake(close_odds, est_winprob) * 100
            suggested_eur = max(10, round(BANKROLL_EUR * 0.02))
            
            all_signals.append({
                "match_id": match_id,
                "home": row.get("home", ""),
                "away": row.get("away", ""),
                "country": country,
                "league": row.get("league", ""),
                "kick_off": row.get("kick_off", ""),
                "match_date": row.get("match_date", TODAY),
                "market": suffix.upper().replace("x", "X"),
                "predicted_outcome": predicted,
                "score": quality,
                "signal_type": rtype,
                "close_odds": close_odds,
                "pct_change": cp,
                "breakdown": " ".join(breakdown),
                "n_snapshots": int(row.get("n_snapshots", 0)),
                "open_overround": round(row.get("open_overround", 1) * 100, 1),
                "close_overround": round(row.get("close_overround", 1) * 100, 1),
                "kelly_pct": round(kelly_pct, 1),
                "suggested_eur": suggested_eur,
                "est_winprob": est_winprob,
            })
    
    # Sort by quality DESC
    all_signals.sort(key=lambda x: (-x["score"], x["kick_off"]))
    
    # Deduplicate: one signal per match_id — keep highest quality
    seen = {}
    deduped = []
    for sig in all_signals:
        mid = sig["match_id"]
        if mid not in seen:
            seen[mid] = sig
            deduped.append(sig)
        elif sig["score"] > seen[mid]["score"]:
            deduped.remove(seen[mid])
            seen[mid] = sig
            deduped.append(sig)
    all_signals = deduped
    
    # Display
    if not quiet:
        print(f"\n  R7 SIGNALS: {len(all_signals)}")
        print("─" * 70)
        
        for sig in all_signals:
            outcome_label = f"{sig['predicted_outcome']} @ {sig['close_odds']:.2f}"
            print(f"\n  [Q{sig['score']:>3}] {sig['home']} vs {sig['away']} | "
                  f"{outcome_label} | {sig['signal_type']}")
            print(f"  League: {sig['league']} ({sig['country']})")
            print(f"  Breakdown: {sig['breakdown']}")
            print(f"  Change: {sig['pct_change']:+.1f}% | Snapshots: {sig['n_snapshots']} | "
                  f"Overround: {sig['open_overround']:.1f}%→{sig['close_overround']:.1f}%")
            print(f"  Kelly: {sig['kelly_pct']:.1f}% | Suggested: 2% flat = €{sig['suggested_eur']}")
        
        if not all_signals:
            print("  Nema R7 signala.")
        print(f"\n{'─' * 70}")
    
    # Save alerts CSV
    if all_signals:
        alerts_path = ALERTS_DIR / f"{STAMP}.csv"
        with open(alerts_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_signals[0].keys()), delimiter=";")
            writer.writeheader()
            writer.writerows(all_signals)
        if not quiet:
            print(f"  Alerts saved: {alerts_path}")
    
    # Log
    log_entry = {
        "timestamp": NOW.isoformat(),
        "threshold": threshold,
        "total_upcoming": len(upcoming),
        "signals_found": len(all_signals),
        "avg_quality": round(np.mean([s["score"] for s in all_signals]), 1) if all_signals else 0,
        "med_speed": round(med_speed, 3),
        "med_counter": round(med_counter, 3),
    }
    log_path = LOGS_DIR / "scan_log.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")
    
    return all_signals


# ═══════════════════════════════════════════════════════════════════════════
#  TRADE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════
def load_trades():
    """Load trades from CSV. Returns list of dicts."""
    if not TRADES_FILE.exists():
        return []
    trades = []
    with open(TRADES_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            trades.append(row)
    return trades


def save_trades(trades):
    """Save trades list to CSV."""
    with open(TRADES_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADES_FIELDS, delimiter=";")
        writer.writeheader()
        for t in trades:
            writer.writerow({k: t.get(k, "") for k in TRADES_FIELDS})


def get_next_trade_id(trades):
    """Get next trade ID."""
    if not trades:
        return 1
    return max(int(t.get("trade_id", 0)) for t in trades) + 1


def record_trade(signal, account="R7"):
    """Record a new paper trade from a signal."""
    trades = load_trades()
    
    # Check if already recorded (same match_id + market)
    for t in trades:
        if (t.get("match_id") == signal["match_id"] and
            t.get("predicted_outcome") == signal["predicted_outcome"] and
            t.get("account") == account):
            return None  # Already exists
    
    trade_id = get_next_trade_id(trades)
    stake_pct = 2.0  # flat 2%
    stake_eur = round(BANKROLL_EUR * stake_pct / 100, 2)
    
    trade = {
        "trade_id": trade_id,
        "date": signal.get("match_date", TODAY),
        "match_id": signal["match_id"],
        "home": signal["home"],
        "away": signal["away"],
        "league": signal["league"],
        "country": signal["country"],
        "predicted_outcome": signal["predicted_outcome"],
        "score_composite": signal["score"],
        "signal_type": signal["signal_type"],
        "odds_at_signal": signal["close_odds"],
        "stake_pct": stake_pct,
        "stake_eur": stake_eur,
        "status": "pending",
        "actual_result": "",
        "actual_score": "",
        "profit_loss": 0,
        "cumulative_pnl": 0,
        "account": account,
        "notes": f"{signal['breakdown']} | Kelly: {signal['kelly_pct']:.1f}%",
    }
    
    trades.append(trade)
    
    # Recalculate cumulative PnL
    _recalc_cumulative(trades, account)
    save_trades(trades)
    
    return trade


def record_signals_as_trades(signals):
    """Record all R7 signals as paper trades (single account)."""
    new_count = 0
    for sig in signals:
        t = record_trade(sig, account="R7")
        if t:
            new_count += 1
    print(f"  Recorded: {new_count} new R7 trades")


def _recalc_cumulative(trades, account=None):
    """Recalculate cumulative PnL for all trades or specific account."""
    for acc in (["R7", "A", "B"] if account is None else [account]):
        acc_trades = [t for t in trades if t.get("account") == acc]
        cum = 0
        for t in acc_trades:
            pl = float(t.get("profit_loss", 0) or 0)
            cum += pl
            t["cumulative_pnl"] = round(cum, 2)


# ═══════════════════════════════════════════════════════════════════════════
#  RESULT UPDATER (--update)
# ═══════════════════════════════════════════════════════════════════════════
def run_update():
    """Update results for pending trades using latest movement data."""
    print("═" * 70)
    print(f"  RESULT UPDATER — {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    print("═" * 70)
    
    trades = load_trades()
    pending = [t for t in trades if t.get("status") == "pending"]
    
    if not pending:
        print("  Nema pending trades za update.")
        return
    
    print(f"  Pending trades: {len(pending)}")
    
    # Load latest movement data to find finished matches
    df = load_movement_data(days_back=3)
    if df.empty:
        print("  [!] Nema movement podataka.")
        return
    
    # Get latest status for each match
    latest = df.sort_values("scraped_at").groupby("match_id").last()
    
    updated = 0
    for trade in pending:
        match_id = trade.get("match_id", "")
        if match_id not in latest.index:
            continue
        
        match_row = latest.loc[match_id]
        status = str(match_row.get("status", "")).strip().lower()
        score = str(match_row.get("score", "")).strip()
        
        if status != "finished" or not score or score == "nan":
            continue
        
        # Parse score "H-A"
        try:
            parts = score.split("-")
            home_goals = int(parts[0].strip())
            away_goals = int(parts[1].strip())
        except (ValueError, IndexError):
            continue
        
        # Determine actual result
        if home_goals > away_goals:
            actual = "HOME"
        elif away_goals > home_goals:
            actual = "AWAY"
        else:
            actual = "DRAW"
        
        predicted = trade.get("predicted_outcome", "")
        odds = float(trade.get("odds_at_signal", 0) or 0)
        stake_eur = float(trade.get("stake_eur", 0) or 0)
        
        if actual == predicted:
            profit = round(stake_eur * (odds - 1), 2)
            trade["status"] = "won"
        else:
            profit = round(-stake_eur, 2)
            trade["status"] = "lost"
        
        trade["actual_result"] = actual
        trade["actual_score"] = score
        trade["profit_loss"] = profit
        updated += 1
        print(f"  {'✅' if trade['status'] == 'won' else '❌'} {trade['home']} vs {trade['away']} "
              f"→ {score} ({actual}) | P/L: {profit:+.2f}€")
    
    # Recalculate cumulative PnL
    _recalc_cumulative(trades)
    
    # Backup before saving
    if TRADES_FILE.exists():
        backup_path = TRADES_DIR / f"trades_backup_{STAMP}.csv"
        shutil.copy2(TRADES_FILE, backup_path)
    
    save_trades(trades)
    print(f"\n  Updated: {updated} trades")
    print(f"  Remaining pending: {len([t for t in trades if t.get('status') == 'pending'])}")


# ═══════════════════════════════════════════════════════════════════════════
#  MONITORING & STOP-LOSS CHECKS
# ═══════════════════════════════════════════════════════════════════════════
def check_stop_rules(trades, account="A"):
    """Check stop/pause rules. Returns (status, alerts)."""
    acc_trades = [t for t in trades if t.get("account") == account]
    resolved = [t for t in acc_trades if t.get("status") in ("won", "lost")]
    
    alerts = []
    status = "OK"  # OK, PAUSE, STOP
    
    n_resolved = len(resolved)
    if n_resolved < STOP_RULES["min_bets_for_eval"]:
        alerts.append(f"ℹ️  Premalo oklada za evaluaciju ({n_resolved}/{STOP_RULES['min_bets_for_eval']})")
        return status, alerts
    
    # Accuracy check (last 50)
    last50 = resolved[-50:]
    wins = sum(1 for t in last50 if t.get("status") == "won")
    acc = wins / len(last50) * 100 if last50 else 0
    
    if acc < STOP_RULES["min_accuracy_50bets"]:
        status = "STOP"
        alerts.append(f"🛑 STOP: Accuracy {acc:.1f}% < {STOP_RULES['min_accuracy_50bets']}% (last 50)")
    
    # Drawdown check
    pnl_series = [float(t.get("profit_loss", 0) or 0) for t in resolved]
    cum = np.cumsum(pnl_series)
    if len(cum) > 0:
        peak = np.maximum.accumulate(cum)
        drawdown = peak - cum
        max_dd = np.max(drawdown) if len(drawdown) > 0 else 0
        total_staked = sum(float(t.get("stake_eur", 0) or 0) for t in resolved)
        dd_pct = (max_dd / BANKROLL_EUR * 100) if BANKROLL_EUR > 0 else 0
        
        if dd_pct > STOP_RULES["max_drawdown_pct"]:
            status = "STOP"
            alerts.append(f"🛑 STOP: MaxDD {dd_pct:.1f}% > {STOP_RULES['max_drawdown_pct']}%")
        elif dd_pct > STOP_RULES["pause_drawdown_pct"]:
            if status != "STOP":
                status = "PAUSE"
            alerts.append(f"⚠️  PAUSE: MaxDD {dd_pct:.1f}% > {STOP_RULES['pause_drawdown_pct']}%")
    
    # Consecutive losses
    consec = 0
    max_consec = 0
    for t in resolved:
        if t.get("status") == "lost":
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    
    if max_consec >= STOP_RULES["max_consecutive_losses"]:
        if status != "STOP":
            status = "PAUSE"
        alerts.append(f"⚠️  PAUSE: {max_consec} consecutive losses ≥ {STOP_RULES['max_consecutive_losses']}")
    
    # Negative weeks
    week_pnl = defaultdict(float)
    for t in resolved:
        d = t.get("date", "")
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            week_key = dt.strftime("%Y-W%W")
            week_pnl[week_key] += float(t.get("profit_loss", 0) or 0)
        except Exception:
            continue
    
    sorted_weeks = sorted(week_pnl.keys())
    if len(sorted_weeks) >= STOP_RULES["negative_weeks"]:
        last_n = sorted_weeks[-STOP_RULES["negative_weeks"]:]
        if all(week_pnl[w] < 0 for w in last_n):
            status = "STOP"
            alerts.append(f"🛑 STOP: {STOP_RULES['negative_weeks']} consecutive negative weeks")
    
    if not alerts:
        alerts.append("✅ All metrics within acceptable range")
    
    return status, alerts


# ═══════════════════════════════════════════════════════════════════════════
#  DAILY REPORT (--report)
# ═══════════════════════════════════════════════════════════════════════════
def run_report():
    """Generate daily dashboard report."""
    print("═" * 70)
    print(f"  DAILY REPORT — {TODAY}")
    print("═" * 70)
    
    trades = load_trades()
    
    for account in ["R7"]:
        acc_trades = [t for t in trades if t.get("account") == account]
        resolved = [t for t in acc_trades if t.get("status") in ("won", "lost")]
        pending = [t for t in acc_trades if t.get("status") == "pending"]
        threshold_label = "Q≥70"
        
        print(f"\n{'─' * 70}")
        print(f"  ACCOUNT {account} ({threshold_label})")
        print(f"{'─' * 70}")
        
        # Yesterday's results
        yesterday = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_trades = [t for t in resolved if t.get("date") == yesterday]
        
        if yesterday_trades:
            y_wins = sum(1 for t in yesterday_trades if t.get("status") == "won")
            y_pnl = sum(float(t.get("profit_loss", 0) or 0) for t in yesterday_trades)
            y_acc = y_wins / len(yesterday_trades) * 100 if yesterday_trades else 0
            
            print(f"\n  YESTERDAY'S RESULTS ({yesterday}):")
            print(f"    Bets placed: {len(yesterday_trades)}")
            print(f"    Won: {y_wins} ({y_acc:.1f}%)")
            print(f"    P/L: {'+' if y_pnl >= 0 else ''}{y_pnl:.0f}€")
        else:
            print(f"\n  YESTERDAY'S RESULTS: No resolved bets")
        
        # Cumulative
        total_pnl = sum(float(t.get("profit_loss", 0) or 0) for t in resolved)
        print(f"    Cumulative P/L: {'+' if total_pnl >= 0 else ''}{total_pnl:.0f}€")
        
        # Rolling stats (last 50)
        if len(resolved) >= 10:
            window = resolved[-50:] if len(resolved) >= 50 else resolved
            w_wins = sum(1 for t in window if t.get("status") == "won")
            w_pnl = [float(t.get("profit_loss", 0) or 0) for t in window]
            w_staked = sum(float(t.get("stake_eur", 0) or 0) for t in window)
            w_total_pnl = sum(w_pnl)
            w_acc = w_wins / len(window) * 100
            w_roi = (w_total_pnl / w_staked * 100) if w_staked > 0 else 0
            
            # MaxDD
            cum = np.cumsum(w_pnl)
            peak = np.maximum.accumulate(cum)
            dd = peak - cum
            max_dd_eur = np.max(dd) if len(dd) > 0 else 0
            max_dd_pct = max_dd_eur / BANKROLL_EUR * 100
            
            # Sharpe (daily returns proxy)
            if len(w_pnl) > 1 and np.std(w_pnl) > 0:
                sharpe = np.mean(w_pnl) / np.std(w_pnl)
            else:
                sharpe = 0
            
            n_label = f"last {len(window)}" if len(resolved) >= 50 else f"all {len(window)}"
            print(f"\n  ROLLING STATS ({n_label} bets):")
            print(f"    Accuracy: {w_acc:.1f}%")
            print(f"    ROI: {w_roi:+.1f}%")
            print(f"    MaxDD: {max_dd_pct:.1f}%")
            print(f"    Sharpe: {sharpe:.2f}")
            
            # On track?
            if w_acc >= 40:
                print(f"\n    ✅ ON TRACK — accuracy above 40% threshold")
            elif w_acc >= 35:
                print(f"\n    ⚠️  WARNING — accuracy {w_acc:.1f}% approaching minimum")
            else:
                print(f"\n    🛑 BELOW THRESHOLD — accuracy {w_acc:.1f}% below 35% minimum")
        else:
            print(f"\n  ROLLING STATS: Need ≥10 resolved bets ({len(resolved)} so far)")
        
        # Today's pending
        today_pending = [t for t in pending if t.get("date") == TODAY]
        if today_pending:
            print(f"\n  TODAY'S PENDING ({TODAY}):")
            for t in today_pending:
                print(f"    [{t.get('score_composite', '')}] {t['home']} vs {t['away']} — "
                      f"{t['predicted_outcome']} @ {t['odds_at_signal']} — {t['signal_type']}")
        
        # Stop rules
        stop_status, alerts = check_stop_rules(trades, account)
        print(f"\n  ALERTS:")
        for a in alerts:
            print(f"    {a}")
    
    # R7 TYPE MONITORING
    print(f"\n{'─' * 70}")
    print(f"  R7 TYPE MONITORING")
    print(f"{'─' * 70}")
    
    r7_trades = [t for t in trades if t.get("account") == "R7"]
    r7_resolved = [t for t in r7_trades if t.get("status") in ("won", "lost")]
    
    if r7_resolved:
        type_counts = defaultdict(int)
        type_wins = defaultdict(int)
        for t in r7_resolved:
            st = t.get("signal_type", "UNKNOWN")
            type_counts[st] += 1
            if t.get("status") == "won":
                type_wins[st] += 1
        
        for st, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            w = type_wins.get(st, 0)
            acc = w / cnt * 100 if cnt > 0 else 0
            print(f"  {st}: {cnt} bets, {w}/{cnt} won ({acc:.1f}%)")
    else:
        print(f"  No resolved R7 trades yet")
    
    # Save report
    report_path = REPORTS_DIR / f"daily_report_{TODAY}.txt"
    # We've been printing to stdout — also save
    print(f"\n{'═' * 70}")
    print(f"  Report saved: {report_path}")
    
    _save_report_file(trades, report_path)


def _save_report_file(trades, path):
    """Save report as structured text file."""
    lines = []
    lines.append(f"DAILY REPORT — {TODAY}")
    lines.append(f"Generated: {NOW.isoformat()}")
    lines.append("")
    
    for acc in ["R7"]:
        acc_trades = [t for t in trades if t.get("account") == acc]
        resolved = [t for t in acc_trades if t.get("status") in ("won", "lost")]
        pending = [t for t in acc_trades if t.get("status") == "pending"]
        
        lines.append(f"ACCOUNT {acc}:")
        lines.append(f"  Total bets: {len(acc_trades)}")
        lines.append(f"  Resolved: {len(resolved)}")
        lines.append(f"  Pending: {len(pending)}")
        
        if resolved:
            wins = sum(1 for t in resolved if t.get("status") == "won")
            pnl = sum(float(t.get("profit_loss", 0) or 0) for t in resolved)
            staked = sum(float(t.get("stake_eur", 0) or 0) for t in resolved)
            
            lines.append(f"  Wins: {wins} ({wins/len(resolved)*100:.1f}%)")
            lines.append(f"  Total P/L: {pnl:+.2f}€")
            lines.append(f"  Total Staked: {staked:.2f}€")
            lines.append(f"  ROI: {pnl/staked*100:+.1f}%" if staked > 0 else "  ROI: N/A")
        lines.append("")
    
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════════
#  WEEKLY VALIDATION (--weekly)
# ═══════════════════════════════════════════════════════════════════════════
def run_weekly():
    """Generate weekly validation report."""
    print("═" * 70)
    print(f"  WEEKLY VALIDATION REPORT — {TODAY}")
    print("═" * 70)
    
    trades = load_trades()
    
    for account in ["R7"]:
        threshold_label = "Q≥70"
        acc_trades = [t for t in trades if t.get("account") == account]
        resolved = [t for t in acc_trades if t.get("status") in ("won", "lost")]
        
        print(f"\n{'─' * 70}")
        print(f"  ACCOUNT {account} ({threshold_label})")
        print(f"{'─' * 70}")
        
        if len(resolved) < 20:
            print(f"  Need ≥20 resolved bets for validation ({len(resolved)} so far)")
            continue
        
        wins = sum(1 for t in resolved if t.get("status") == "won")
        n = len(resolved)
        actual_acc = wins / n
        expected = 0.449  # R7 backtest accuracy
        
        # 1. Binomial test
        try:
            binom_result = scipy_stats.binomtest(wins, n, expected, alternative='two-sided')
            p_val = binom_result.pvalue
        except Exception:
            # Fallback for older scipy
            from scipy.stats import binom_test
            p_val = binom_test(wins, n, expected)

        print(f"\n  1. ACCURACY vs EXPECTED:")
        print(f"     Actual: {actual_acc*100:.1f}% ({wins}/{n})")
        print(f"     Expected: {expected*100:.1f}%")
        print(f"     Binomial p-value: {p_val:.4f}")
        if p_val < 0.05:
            if actual_acc > expected:
                print(f"     ✅ Significantly BETTER than expected!")
            else:
                print(f"     ⚠️  Significantly WORSE than expected")
        else:
            print(f"     ℹ️  Not significantly different (within expected range)")
        
        # 2. Feature drift — score distribution
        scores = [float(t.get("score_composite", 0) or 0) for t in resolved]
        first_half = scores[:len(scores)//2]
        second_half = scores[len(scores)//2:]
        
        print(f"\n  2. SCORE DISTRIBUTION DRIFT:")
        print(f"     First half avg: {np.mean(first_half):.1f}")
        print(f"     Second half avg: {np.mean(second_half):.1f}")
        if abs(np.mean(first_half) - np.mean(second_half)) > 3:
            print(f"     ⚠️  Score drift detected!")
        else:
            print(f"     ✅ Stable")
        
        # 3. Signal quality
        print(f"\n  3. SIGNAL QUALITY:")
        avg_score = np.mean(scores) if scores else 0
        print(f"     Average composite score: {avg_score:.1f}")
        
        # 4. Signal type distribution
        print(f"\n  4. SIGNAL TYPE DISTRIBUTION:")
        type_counts = defaultdict(int)
        type_wins = defaultdict(int)
        for t in resolved:
            st = t.get("signal_type", "UNKNOWN")
            type_counts[st] += 1
            if t.get("status") == "won":
                type_wins[st] += 1
        
        for st, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            w = type_wins.get(st, 0)
            print(f"     {st}: {cnt} bets, {w}/{cnt} won ({w/cnt*100:.1f}%)")
        
        # 5. League distribution
        print(f"\n  5. LEAGUE DISTRIBUTION (top 10):")
        league_counts = defaultdict(int)
        for t in resolved:
            league_counts[t.get("league", "?")] += 1
        
        for lg, cnt in sorted(league_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"     {lg}: {cnt}")
        
        # 6. Weekly PnL
        print(f"\n  6. WEEKLY P/L:")
        week_pnl = defaultdict(float)
        week_count = defaultdict(int)
        for t in resolved:
            d = t.get("date", "")
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                wk = dt.strftime("%Y-W%W")
                week_pnl[wk] += float(t.get("profit_loss", 0) or 0)
                week_count[wk] += 1
            except Exception:
                continue
        
        for wk in sorted(week_pnl.keys()):
            pnl = week_pnl[wk]
            cnt = week_count[wk]
            marker = "✅" if pnl >= 0 else "❌"
            print(f"     {wk}: {pnl:+.0f}€ ({cnt} bets) {marker}")
        
        # 7. Recommendation
        print(f"\n  7. RECOMMENDATION:")
        stop_status, alerts = check_stop_rules(trades, account)
        
        if stop_status == "STOP":
            print(f"     🛑 STOP — Critical threshold breached")
        elif stop_status == "PAUSE":
            print(f"     ⚠️  PAUSE — Review before continuing")
        elif actual_acc >= 0.40 and p_val >= 0.05:
            print(f"     ✅ CONTINUE — All metrics acceptable")
        elif actual_acc >= 0.40 and p_val < 0.05 and actual_acc > expected:
            print(f"     ✅ CONTINUE — Outperforming expectations!")
        else:
            print(f"     ⚠️  CAUTION — Monitor closely")
    
    # Save weekly report
    report_path = REPORTS_DIR / f"weekly_report_{TODAY}.txt"
    print(f"\n{'═' * 70}")
    print(f"  Weekly report timestamp: {NOW.isoformat()}")


# ═══════════════════════════════════════════════════════════════════════════
#  WATCH MODE (--watch)
# ═══════════════════════════════════════════════════════════════════════════
def run_watch(threshold=70, interval=180):
    """Continuous monitoring: scan every N seconds, record new trades."""
    print("═" * 70)
    print(f"  WATCH MODE — scanning every {interval}s (Ctrl+C to stop)")
    print(f"  Threshold: ≥{threshold}")
    print("═" * 70)
    
    seen_alerts = set()
    cycle = 0
    
    try:
        while True:
            cycle += 1
            now = datetime.now()
            print(f"\n  [Cycle {cycle}] {now.strftime('%H:%M:%S')}")
            
            signals = run_scan(threshold=threshold, quiet=True)
            
            new_signals = []
            for s in signals:
                alert_key = f"{s['match_id']}|{s['market']}"
                if alert_key not in seen_alerts:
                    seen_alerts.add(alert_key)
                    new_signals.append(s)
            
            if new_signals:
                print(f"  🆕 {len(new_signals)} new signals:")
                for s in new_signals:
                    print(f"    [{s['score']}] {s['home']} vs {s['away']} | "
                          f"{s['predicted_outcome']} @ {s['close_odds']:.2f} | {s['signal_type']}")
                
                # Auto-record trades
                record_signals_as_trades(new_signals)
            else:
                print(f"  No new signals (total tracked: {len(seen_alerts)})")
            
            # Check CSV directory for new files
            today_files = sorted(DATA_DIR.glob(f"{now.strftime('%Y-%m-%d')}*.csv"))
            print(f"  CSV files today: {len(today_files)}")
            
            print(f"  Next scan: {(now + timedelta(seconds=interval)).strftime('%H:%M:%S')}")
            time.sleep(interval)
    
    except KeyboardInterrupt:
        print(f"\n  Watch mode stopped. Total alerts: {len(seen_alerts)}")


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="R7 Paper Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python trader.py --scan                 Scan and display R7 signals
  python trader.py --scan --threshold 75  Only quality ≥75
  python trader.py --scan --record        Scan + auto-record paper trades
  python trader.py --report               Daily dashboard
  python trader.py --update               Update finished match results
  python trader.py --weekly               Weekly validation report
  python trader.py --watch                Continuous monitoring (3 min interval)
        """,
    )
    
    parser.add_argument("--scan", action="store_true", help="Scan upcoming matches for R7 signals")
    parser.add_argument("--report", action="store_true", help="Generate daily report")
    parser.add_argument("--update", action="store_true", help="Update results for finished matches")
    parser.add_argument("--weekly", action="store_true", help="Generate weekly validation report")
    parser.add_argument("--watch", action="store_true", help="Continuous monitoring mode")
    parser.add_argument("--threshold", type=int, default=70, help="Minimum R7 quality (default: 70)")
    parser.add_argument("--record", action="store_true", help="Auto-record signals as paper trades")
    parser.add_argument("--interval", type=int, default=180, help="Watch interval in seconds (default: 180)")
    
    args = parser.parse_args()
    
    if not any([args.scan, args.report, args.update, args.weekly, args.watch]):
        parser.print_help()
        return
    
    if args.scan:
        signals = run_scan(threshold=args.threshold)
        if args.record and signals:
            record_signals_as_trades(signals)
    
    if args.update:
        run_update()
    
    if args.report:
        run_report()
    
    if args.weekly:
        run_weekly()
    
    if args.watch:
        run_watch(threshold=args.threshold, interval=args.interval)


if __name__ == "__main__":
    main()
