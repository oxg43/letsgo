#!/usr/bin/env python3
"""
=============================================================================
  GENERATE DAILY ONLY_HOME TSV
  
  Lagana skripta za dnevnu generaciju ONLY_HOME signala.
  Učitava samo zadnjih 7 dana CSV-ova za brzinu.
  
  Pokreni ručno:   python generate_daily_only_home.py
  Ili automatski:  Scheduled Task svaki dan u 06:00
=============================================================================
"""

import os, sys, warnings
from pathlib import Path
from datetime import datetime, timedelta, time as dtime
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# ─── Putanje ────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "odds_data" / "movement"
SIGNAL_DIR = BASE_DIR / "odds_data" / "signal_map"
SIGNAL_DIR.mkdir(parents=True, exist_ok=True)

NOW   = datetime.now()
TODAY = NOW.strftime("%Y-%m-%d")

# ─── Kolone za output ───────────────────────────────────────────────────
COLUMNS = [
    "captured_at", "match_date", "kick_off", "match", "bet", "odds",
    "opening", "drop_pct", "spread", "n_snapshots", "monitoring_h",
    "strategies", "confidence", "stake_pct", "monotonic", "overround", "reason"
]


def load_recent_data(days=7):
    """Učitaj samo CSV-ove iz zadnjih N dana za brzinu."""
    csv_files = sorted(DATA_DIR.glob("*.csv"))
    if not csv_files:
        print("  [!] Nema CSV datoteka!")
        return pd.DataFrame()
    
    # Filter samo novije datoteke
    cutoff = NOW - timedelta(days=days)
    recent_files = []
    for fp in csv_files:
        try:
            # Parse datum iz imena: 2026-02-17_162445_cycle1.csv
            date_str = fp.name[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff.replace(hour=0, minute=0, second=0):
                recent_files.append(fp)
        except:
            continue
    
    print(f"  Učitavam {len(recent_files)} CSV-ova (zadnjih {days} dana)...")
    
    frames = []
    for fp in recent_files:
        try:
            tmp = pd.read_csv(fp, sep=";", dtype=str)
            tmp["source_file"] = fp.name
            frames.append(tmp)
        except Exception as e:
            print(f"  [WARN] Skip {fp.name}: {e}")
    
    if not frames:
        return pd.DataFrame()
    
    df = pd.concat(frames, ignore_index=True)
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    
    for col in ["odds_1", "odds_x", "odds_2"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    df["match_key"] = df["kick_off"].astype(str) + "|" + df["home"].astype(str) + "|" + df["away"].astype(str)
    
    return df


def compute_signals(df):
    """Izračunaj ONLY_HOME signale."""
    
    # Filtriraj upcoming mečeve
    df_upcoming = df[df["status"].str.strip().str.lower() == "upcoming"].copy()
    if len(df_upcoming) == 0:
        return []
    
    # Grupiraj po meču
    signals = []
    
    for match_key, grp in df_upcoming.groupby("match_key"):
        grp = grp.sort_values("scraped_at")
        
        # Osnovni info
        last_row = grp.iloc[-1]
        first_row = grp.iloc[0]
        
        kick_off = str(last_row.get("kick_off", ""))
        home = str(last_row.get("home", ""))
        away = str(last_row.get("away", ""))
        
        # Fix: match_date može biti NaN u CSV-u, koristi kick_off za parsing datuma
        raw_date = last_row.get("match_date", "")
        if pd.isna(raw_date) or str(raw_date).lower() in ["nan", "", "none"]:
            # Pokušaj izvući datum iz kick_off (format: "21.02. 20:00" ili "2026-02-21 20:00")
            ko_str = str(kick_off)
            try:
                if "." in ko_str and " " in ko_str:
                    # Format: "21.02. 20:00"
                    parts = ko_str.split()[0]  # "21.02."
                    day, month = parts.rstrip(".").split(".")
                    match_date = f"{NOW.year}-{int(month):02d}-{int(day):02d}"
                else:
                    match_date = TODAY
            except:
                match_date = TODAY
        else:
            match_date = str(raw_date)
        
        # Kvote
        open_1 = pd.to_numeric(first_row.get("odds_1"), errors="coerce")
        close_1 = pd.to_numeric(last_row.get("odds_1"), errors="coerce")
        close_x = pd.to_numeric(last_row.get("odds_x"), errors="coerce")
        close_2 = pd.to_numeric(last_row.get("odds_2"), errors="coerce")
        
        if pd.isna(open_1) or pd.isna(close_1) or close_1 <= 1:
            continue
        
        # Drop %
        drop_pct = (open_1 - close_1) / open_1 * 100 if open_1 > 0 else 0
        
        # Spread (razlika između 1 i 2)
        spread = abs(close_1 - close_2) if not pd.isna(close_2) else 0
        
        # Overround
        overround = 0
        if close_1 > 1 and close_x > 1 and close_2 > 1:
            overround = 1/close_1 + 1/close_x + 1/close_2
        
        # Monitoring info
        n_snaps = len(grp)
        first_seen = grp["scraped_at"].min()
        last_seen = grp["scraped_at"].max()
        mon_hours = (last_seen - first_seen).total_seconds() / 3600 if pd.notna(first_seen) and pd.notna(last_seen) else 0
        
        # Monotonicity (koliko konzistentno pada)
        odds_vals = grp["odds_1"].dropna().astype(float).values
        if len(odds_vals) >= 3:
            changes = np.diff(odds_vals)
            total_abs = np.sum(np.abs(changes))
            monotonic = abs(np.sum(changes)) / total_abs if total_abs > 0 else 1
        else:
            monotonic = 0
        
        # ═══════════════════════════════════════════════════════════════
        #  QUALITY FILTERS (novi tweakovi)
        # ═══════════════════════════════════════════════════════════════
        
        # Filter 1: Skip noisy trends (monotonic < 0.3 = kvote osciliraju)
        if monotonic < 0.3 and len(odds_vals) >= 5:
            continue
        
        # ═══════════════════════════════════════════════════════════════
        #  STRATEGIJE (ONLY_HOME)
        # ═══════════════════════════════════════════════════════════════
        strats = []
        
        # S1: Favorit [1.50-2.00] + Drop >= 3%
        if 1.50 <= close_1 <= 2.00 and drop_pct >= 3:
            strats.append("S1")
        
        # S2: Slight Favorite spread[1-3] + Drop >= 5%
        if 1 <= spread <= 3 and drop_pct >= 5:
            strats.append("S2")
        
        # S3: Home Favorite + Drop >= 10%
        if close_1 < close_2 and drop_pct >= 10:
            strats.append("S3")
        
        # COMBO: S1 ∩ S2
        if "S1" in strats and "S2" in strats:
            strats.append("COMBO")
        
        if not strats:
            continue
        
        # Confidence score
        conf = 50
        if "COMBO" in strats:
            conf += 30
        if drop_pct >= 10:
            conf += 15
        if monotonic >= 0.5:
            conf += 5
        conf = min(conf, 100)
        
        # Filter 2: Skip high odds (>3.0) + low confidence (<70%)
        # -> Ovi matches imaju male šanse za prolaz
        if close_1 > 3.0 and conf < 70:
            continue
        
        # Stake
        stake = "5%" if conf >= 80 else ("3%" if conf >= 60 else "2%")
        
        # Build signal row
        signal = {
            "captured_at": NOW.strftime("%H:%M:%S"),
            "match_date": match_date,
            "kick_off": kick_off,
            "match": f"{home} vs {away}",
            "bet": "HOME WIN (1)",
            "odds": round(close_1, 2),
            "opening": round(open_1, 2),
            "drop_pct": f"+{drop_pct:.1f}%",
            "spread": round(spread, 2),
            "n_snapshots": n_snaps,
            "monitoring_h": round(mon_hours, 1),
            "strategies": ",".join(strats),
            "confidence": f"{conf}%",
            "stake_pct": stake,
            "monotonic": round(monotonic, 3),
            "overround": round(overround, 4),
            "reason": f"Strategies: {','.join(strats)} | Drop: +{drop_pct:.1f}% | Spread: {spread:.2f}"
        }
        signals.append(signal)
    
    return signals


def main():
    print("=" * 72)
    print(f"  DAILY ONLY_HOME GENERATOR — {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)
    print()
    
    # Load data
    df = load_recent_data(days=7)
    if len(df) == 0:
        print("  [!] Nema podataka!")
        return
    
    print(f"  Učitano {len(df)} redova")
    print()
    
    # Compute signals
    print("  Računam signale...")
    signals = compute_signals(df)
    
    if not signals:
        print("  [!] Nema ONLY_HOME signala!")
        return
    
    # Sort by confidence desc, then kick_off
    signals_df = pd.DataFrame(signals)
    signals_df["conf_num"] = signals_df["confidence"].str.replace("%", "").astype(int)
    signals_df = signals_df.sort_values(["match_date", "kick_off", "conf_num"], ascending=[True, True, False])
    signals_df = signals_df.drop(columns=["conf_num"])
    
    # Filter today's matches
    today_signals = signals_df[signals_df["match_date"] == TODAY]
    upcoming_signals = signals_df[signals_df["match_date"] >= TODAY]
    
    # Save today's file
    today_file = SIGNAL_DIR / f"ONLY_HOME_{TODAY}.tsv"
    if len(today_signals) > 0:
        today_signals[COLUMNS].to_csv(today_file, sep="\t", index=False)
        print(f"\n  ✓ Spremljeno {len(today_signals)} signala za danas: {today_file.name}")
    else:
        print(f"\n  [!] Nema signala za danas ({TODAY})")
    
    # Save upcoming file
    upcoming_file = SIGNAL_DIR / "ONLY_HOME_upcoming.tsv"
    upcoming_signals[COLUMNS].to_csv(upcoming_file, sep="\t", index=False)
    print(f"  ✓ Spremljeno {len(upcoming_signals)} ukupnih signala: {upcoming_file.name}")
    
    # Print summary
    print()
    print("  ─" * 36)
    print(f"  DANAS ({TODAY}) - {len(today_signals)} signala:")
    print("  ─" * 36)
    
    for _, r in today_signals.iterrows():
        print(f"  {r['kick_off']}  {r['match'][:45]:<47} {r['odds']}  {r['strategies']}")
    
    print()


if __name__ == "__main__":
    main()
