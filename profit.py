#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  PROFIT MODE SIGNAL GENERATOR
  
  Jednostavan sustav za generiranje SAMO profitabilnih signala.
  Baziran na analizi 81 paper tradea - koristi samo obrasce koji zarađuju.
  
  AKTIVNI SIGNALI:
    ✅ HOME (tip 1): Drop >=10% ili <6% (favoriti)
    ✅ DRAW (tip X): Drop >=10% 
    ❌ AWAY (tip 2): ISKLJUČEN (-17.5% ROI)
    ❌ Drop 6-10%: ISKLJUČEN (-34.5% ROI)
    ❌ Odds 1.30-1.50: ISKLJUČEN (-29% edge)
  
  OUTPUT:
    - profit.tsv      (glavni file za beting)
    - paper_trades_log.csv (auto paper trade)
  
  USAGE:
    python profit.py              # Pokreni jednom
    python profit.py --loop       # Konstantno praćenje (svakih 5 min)
    python profit.py --resolve    # Resolve paper trades iz rezultata
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import csv
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════════
#  PATHS
# ═══════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "odds_data" / "movement"
SIGNAL_DIR = BASE_DIR / "odds_data" / "signal_map"
SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_TSV = SIGNAL_DIR / "profit.tsv"
PAPER_LOG = BASE_DIR / "paper_trades_log.csv"
ALERTED_FILE = BASE_DIR / "odds_data" / "profit_alerted.json"

NOW = datetime.now()
TODAY = NOW.strftime("%Y-%m-%d")

# ═══════════════════════════════════════════════════════════════════
#  PROFIT MODE FILTERI (iz analize)
# ═══════════════════════════════════════════════════════════════════

# Dokazano profitabilni obrasci
PROFIT_RULES = {
    '1': {  # HOME
        'enabled': True,
        'min_drop_pct': 3.0,
        'allowed_drop_ranges': [(0, 6), (10, 100)],  # Skip 6-10% (-34.5% ROI)
        'banned_odds': (1.30, 1.50),  # -29% edge
        'max_odds': 3.00,
        'min_confidence': 80,
    },
    'X': {  # DRAW
        'enabled': True,
        'min_drop_pct': 10.0,
        'allowed_drop_ranges': [(10, 100)],
        'odds_range': (2.20, 3.50),
        'min_confidence': 80,
    },
    '2': {  # AWAY
        'enabled': False,  # DISABLED! -17.5% ROI
    },
}

# ═══════════════════════════════════════════════════════════════════
#  TSV COLUMNS
# ═══════════════════════════════════════════════════════════════════

TSV_COLUMNS = [
    "time", "date", "kick_off", "match", "bet", "tip",
    "odds", "opening", "drop_pct", "confidence", "stake",
    "tier", "snapshots", "status", "reason"
]

PAPER_COLUMNS = [
    "alerted_at", "match_key", "home", "away", "kick_off",
    "signal_type", "bet", "odds", "opening_odds", "pct_change",
    "confidence", "edge", "snapshots", "stake_model", "stake",
    "result", "profit", "resolved_at", "reason"
]


# ═══════════════════════════════════════════════════════════════════
#  DATA LOADING
# ═══════════════════════════════════════════════════════════════════

def load_movement_data(days=3):
    """Učitaj movement CSV-ove za zadnjih N dana."""
    csv_files = sorted(DATA_DIR.glob("*.csv"))
    if not csv_files:
        return pd.DataFrame()
    
    cutoff = NOW - timedelta(days=days)
    recent = []
    
    for fp in csv_files:
        try:
            date_str = fp.name[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff.replace(hour=0, minute=0, second=0):
                recent.append(fp)
        except:
            continue
    
    if not recent:
        return pd.DataFrame()
    
    frames = []
    for fp in recent:
        try:
            df = pd.read_csv(fp, sep=";", dtype=str)
            df["source_file"] = fp.name
            frames.append(df)
        except:
            continue
    
    if not frames:
        return pd.DataFrame()
    
    df = pd.concat(frames, ignore_index=True)
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    
    for col in ["odds_1", "odds_x", "odds_2"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    df["match_key"] = df["kick_off"].astype(str) + "|" + df["home"].astype(str) + "|" + df["away"].astype(str)
    
    return df


def load_alerted():
    """Load already alerted matches."""
    if ALERTED_FILE.exists():
        try:
            with open(ALERTED_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()


def save_alerted(alerted):
    """Save alerted matches."""
    ALERTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTED_FILE, "w") as f:
        json.dump(list(alerted), f)


# ═══════════════════════════════════════════════════════════════════
#  SIGNAL DETECTION (PROFIT MODE)
# ═══════════════════════════════════════════════════════════════════

def is_drop_allowed(drop_pct, outcome):
    """Check if drop is in allowed range for this outcome."""
    rules = PROFIT_RULES.get(outcome, {})
    allowed = rules.get('allowed_drop_ranges', [(0, 100)])
    
    for lo, hi in allowed:
        if lo <= drop_pct < hi:
            return True
    return False


def is_odds_banned(odds, outcome):
    """Check if odds are in banned zone."""
    rules = PROFIT_RULES.get(outcome, {})
    banned = rules.get('banned_odds')
    
    if banned:
        lo, hi = banned
        if lo <= odds < hi:
            return True
    return False


def compute_confidence(drop_pct, monotonic, n_snaps, outcome):
    """Compute confidence score (0-100)."""
    conf = 50
    
    # Base from drop
    if drop_pct >= 20:
        conf += 30
    elif drop_pct >= 15:
        conf += 25
    elif drop_pct >= 10:
        conf += 20
    elif drop_pct >= 5:
        conf += 10
    else:
        conf += 5
    
    # Monotonic bonus
    if monotonic >= 0.8:
        conf += 10
    elif monotonic >= 0.5:
        conf += 5
    
    # Snapshots bonus
    if n_snaps >= 100:
        conf += 5
    
    # DRAW penalty (smaller sample historical)
    if outcome == 'X':
        conf -= 5
    
    return min(conf, 100)


def detect_signals(df):
    """Detect PROFIT MODE signals."""
    df_upcoming = df[df["status"].str.strip().str.lower() == "upcoming"].copy()
    if len(df_upcoming) == 0:
        return []
    
    signals = []
    
    for match_key, grp in df_upcoming.groupby("match_key"):
        grp = grp.sort_values("scraped_at")
        
        first_row = grp.iloc[0]
        last_row = grp.iloc[-1]
        
        # Match info
        kick_off = str(last_row.get("kick_off", ""))
        home = str(last_row.get("home", ""))
        away = str(last_row.get("away", ""))
        
        # Parse date
        raw_date = last_row.get("match_date", "")
        if pd.isna(raw_date) or str(raw_date).lower() in ["nan", "", "none"]:
            try:
                ko_str = str(kick_off)
                if "." in ko_str and " " in ko_str:
                    parts = ko_str.split()[0]
                    day, month = parts.rstrip(".").split(".")
                    match_date = f"{NOW.year}-{int(month):02d}-{int(day):02d}"
                else:
                    match_date = TODAY
            except:
                match_date = TODAY
        else:
            match_date = str(raw_date)
        
        # Odds
        open_1 = pd.to_numeric(first_row.get("odds_1"), errors="coerce")
        close_1 = pd.to_numeric(last_row.get("odds_1"), errors="coerce")
        open_x = pd.to_numeric(first_row.get("odds_x"), errors="coerce")
        close_x = pd.to_numeric(last_row.get("odds_x"), errors="coerce")
        open_2 = pd.to_numeric(first_row.get("odds_2"), errors="coerce")
        close_2 = pd.to_numeric(last_row.get("odds_2"), errors="coerce")
        
        # Snapshots
        n_snaps = len(grp)
        
        # Monotonicity (za sve ishode)
        def calc_monotonic(col):
            vals = grp[col].dropna().astype(float).values
            if len(vals) >= 3:
                changes = np.diff(vals)
                total_abs = np.sum(np.abs(changes))
                return abs(np.sum(changes)) / total_abs if total_abs > 0 else 1
            return 0
        
        # ═══════════════════════════════════════════════════════════
        #  CHECK EACH OUTCOME
        # ═══════════════════════════════════════════════════════════
        
        outcomes_to_check = [
            ('1', open_1, close_1, 'odds_1', 'HOME'),
            ('X', open_x, close_x, 'odds_x', 'DRAW'),
            # ('2', open_2, close_2, 'odds_2', 'AWAY'),  # DISABLED!
        ]
        
        for outcome, open_odds, close_odds, col, tip_name in outcomes_to_check:
            rules = PROFIT_RULES.get(outcome, {})
            
            # Skip disabled
            if not rules.get('enabled', False):
                continue
            
            if pd.isna(open_odds) or pd.isna(close_odds) or close_odds <= 1:
                continue
            
            # Calculate drop
            drop_pct = (open_odds - close_odds) / open_odds * 100 if open_odds > 0 else 0
            
            # Skip if drop too small
            min_drop = rules.get('min_drop_pct', 3.0)
            if drop_pct < min_drop:
                continue
            
            # Skip banned drop range (6-10%)
            if not is_drop_allowed(drop_pct, outcome):
                continue
            
            # Skip banned odds (1.30-1.50)
            if is_odds_banned(close_odds, outcome):
                continue
            
            # Check max odds
            max_odds = rules.get('max_odds', 5.0)
            if close_odds > max_odds:
                continue
            
            # Check odds range (for DRAW)
            odds_range = rules.get('odds_range')
            if odds_range:
                lo, hi = odds_range
                if not (lo <= close_odds <= hi):
                    continue
            
            # Monotonicity
            monotonic = calc_monotonic(col)
            
            # Confidence
            confidence = compute_confidence(drop_pct, monotonic, n_snaps, outcome)
            
            # Skip low confidence
            min_conf = rules.get('min_confidence', 80)
            if confidence < min_conf:
                continue
            
            # Determine tier
            if drop_pct >= 20:
                tier = 1
            elif drop_pct >= 10:
                tier = 1 if outcome == '1' else 2
            else:
                tier = 2
            
            # Stake based on tier and confidence
            if tier == 1 and confidence >= 90:
                stake = "5%"
            elif tier == 1:
                stake = "4%"
            elif confidence >= 85:
                stake = "3%"
            else:
                stake = "2%"
            
            # Build signal
            signal = {
                "time": NOW.strftime("%H:%M:%S"),
                "date": match_date,
                "kick_off": kick_off,
                "match": f"{home} vs {away}",
                "match_key": f"{home}|{away}",
                "home": home,
                "away": away,
                "bet": tip_name,
                "tip": outcome,
                "odds": round(close_odds, 2),
                "opening": round(open_odds, 2),
                "drop_pct": round(drop_pct, 1),
                "confidence": confidence,
                "stake": stake,
                "tier": tier,
                "snapshots": n_snaps,
                "monotonic": round(monotonic, 3),
                "status": "PENDING",
                "reason": f"Drop {drop_pct:.1f}% | Conf {confidence}% | {n_snaps} snaps"
            }
            signals.append(signal)
    
    return signals


# ═══════════════════════════════════════════════════════════════════
#  OUTPUT: profit.tsv
# ═══════════════════════════════════════════════════════════════════

def write_profit_tsv(signals):
    """Write signals to profit.tsv"""
    df = pd.DataFrame(signals)
    
    if len(df) == 0:
        # Write empty file with headers
        with open(OUTPUT_TSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter='\t')
            writer.writerow(TSV_COLUMNS)
        return
    
    # Sort by date, kick_off, tier
    df = df.sort_values(["date", "kick_off", "tier"])
    
    # Select columns
    output_cols = [c for c in TSV_COLUMNS if c in df.columns]
    df[output_cols].to_csv(OUTPUT_TSV, sep='\t', index=False)


# ═══════════════════════════════════════════════════════════════════
#  OUTPUT: paper_trades_log.csv (AUTO PAPER TRADE)
# ═══════════════════════════════════════════════════════════════════

def add_paper_trade(signal):
    """Add a signal to paper trades log."""
    # Check if file exists and has headers
    file_exists = PAPER_LOG.exists()
    
    row = {
        "alerted_at": datetime.now().isoformat(),
        "match_key": signal["match_key"],
        "home": signal["home"],
        "away": signal["away"],
        "kick_off": signal["kick_off"],
        "signal_type": "PROFIT",
        "bet": signal["tip"],
        "odds": signal["odds"],
        "opening_odds": signal["opening"],
        "pct_change": -signal["drop_pct"] / 100,  # Negative = drop
        "confidence": signal["confidence"] / 100,
        "edge": signal["drop_pct"] / 100,
        "snapshots": signal["snapshots"],
        "stake_model": "kelly_quarter",
        "stake": float(signal["stake"].replace("%", "")) / 100,
        "result": "",
        "profit": "",
        "resolved_at": "",
        "reason": signal["reason"]
    }
    
    with open(PAPER_LOG, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=PAPER_COLUMNS, delimiter=';')
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def process_new_signals(signals, alerted):
    """Process signals - add new ones to paper trades."""
    new_signals = []
    
    for sig in signals:
        key = f"{sig['match_key']}_{sig['tip']}"
        
        if key not in alerted:
            new_signals.append(sig)
            alerted.add(key)
            add_paper_trade(sig)
            print(f"  📊 NEW: {sig['match']} | {sig['bet']} @ {sig['odds']} | Drop {sig['drop_pct']}%")
    
    return new_signals, alerted


# ═══════════════════════════════════════════════════════════════════
#  RESOLVE PAPER TRADES
# ═══════════════════════════════════════════════════════════════════

def resolve_paper_trades():
    """Resolve paper trades from finished matches."""
    if not PAPER_LOG.exists():
        print("  No paper trades to resolve.")
        return
    
    # Read paper trades
    trades = []
    with open(PAPER_LOG, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        trades = list(reader)
    
    if not trades:
        print("  No paper trades to resolve.")
        return
    
    # Load movement data to find results
    df = load_movement_data(days=7)
    if len(df) == 0:
        print("  No movement data for resolving.")
        return
    
    # Get finished matches
    df_finished = df[df["status"].str.strip().str.lower() != "upcoming"]
    
    resolved_count = 0
    for trade in trades:
        if trade.get("result"):  # Already resolved
            continue
        
        match_key = trade.get("match_key", "")
        bet = trade.get("bet", "")
        
        # Find this match in finished data
        home, _, away = match_key.partition("|")
        match_df = df_finished[
            (df_finished["home"].str.strip() == home.strip()) & 
            (df_finished["away"].str.strip() == away.strip())
        ]
        
        if len(match_df) == 0:
            continue
        
        # Get final score
        last = match_df.iloc[-1]
        status = str(last.get("status", "")).strip()
        
        # Parse score from status (e.g., "2:1" or "Final 2-1")
        result = None
        if ":" in status:
            try:
                h, a = status.split(":")
                h_score, a_score = int(h.strip()), int(a.strip())
                if h_score > a_score:
                    result = "1"
                elif h_score < a_score:
                    result = "2"
                else:
                    result = "X"
            except:
                pass
        
        if result:
            odds = float(trade.get("odds", 1))
            stake = float(trade.get("stake", 0.01))
            
            if bet == result:
                trade["result"] = "win"
                trade["profit"] = round((odds - 1) * stake, 4)
            else:
                trade["result"] = "loss"
                trade["profit"] = -stake
            
            trade["resolved_at"] = datetime.now().isoformat()
            resolved_count += 1
    
    # Write back
    with open(PAPER_LOG, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=PAPER_COLUMNS, delimiter=';')
        writer.writeheader()
        writer.writerows(trades)
    
    print(f"  ✓ Resolved {resolved_count} paper trades.")


# ═══════════════════════════════════════════════════════════════════
#  STATS
# ═══════════════════════════════════════════════════════════════════

def print_stats():
    """Print paper trade statistics."""
    if not PAPER_LOG.exists():
        return
    
    trades = []
    with open(PAPER_LOG, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        trades = list(reader)
    
    if not trades:
        return
    
    resolved = [t for t in trades if t.get("result")]
    pending = [t for t in trades if not t.get("result")]
    
    wins = len([t for t in resolved if t.get("result") == "win"])
    losses = len([t for t in resolved if t.get("result") == "loss"])
    
    total_profit = sum(float(t.get("profit", 0) or 0) for t in resolved)
    
    print()
    print("  ═" * 35)
    print(f"  PROFIT MODE STATS")
    print("  ═" * 35)
    print(f"  Total trades: {len(trades)}")
    print(f"  Pending: {len(pending)}")
    print(f"  Resolved: {len(resolved)}")
    if resolved:
        win_rate = wins / len(resolved) * 100
        print(f"  Wins: {wins} ({win_rate:.1f}%)")
        print(f"  Losses: {losses}")
        print(f"  Net Profit: {total_profit:+.2f} units")


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="PROFIT MODE Signal Generator")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--resolve", action="store_true", help="Resolve paper trades")
    parser.add_argument("--stats", action="store_true", help="Show stats only")
    args = parser.parse_args()
    
    print("=" * 70)
    print(f"  PROFIT MODE — {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()
    print("  Aktivni signali:")
    print("    ✅ HOME (1): Drop >=10% ili <6%")
    print("    ✅ DRAW (X): Drop >=10%")
    print("    ❌ AWAY (2): DISABLED")
    print("    ❌ Drop 6-10%: BLOCKED")
    print("    ❌ Odds 1.30-1.50: BLOCKED")
    print()
    
    if args.stats:
        print_stats()
        return
    
    if args.resolve:
        print("  Resolving paper trades...")
        resolve_paper_trades()
        print_stats()
        return
    
    # Load alerted matches
    alerted = load_alerted()
    
    def run_cycle():
        nonlocal alerted
        
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Loading movement data...")
        df = load_movement_data(days=3)
        
        if len(df) == 0:
            print("  [!] Nema podataka!")
            return
        
        print(f"  Loaded {len(df)} rows")
        
        # Detect signals
        signals = detect_signals(df)
        print(f"  Found {len(signals)} PROFIT signals")
        
        # Process new signals
        new_signals, alerted = process_new_signals(signals, alerted)
        
        # Save alerted
        save_alerted(alerted)
        
        # Write profit.tsv (ALL signals, not just new)
        write_profit_tsv(signals)
        print(f"  ✓ profit.tsv updated with {len(signals)} signals")
        
        # Try to resolve
        resolve_paper_trades()
        
        # Stats
        print_stats()
    
    if args.loop:
        print("  Running in loop mode (Ctrl+C to stop)...")
        while True:
            run_cycle()
            print()
            print("  Sleeping 5 minutes...")
            import time
            time.sleep(300)
    else:
        run_cycle()
    
    print()
    print("  ✓ Done!")


if __name__ == "__main__":
    main()
