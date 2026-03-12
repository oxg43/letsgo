#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  CORRECTION_UP TRACKER
  
  Prati isključivo CORRECTION_UP signal — jedini signal s dokazanom točnošću
  (EWMA=68.9%, N=28 historijski, adaptivna težina 70.7).

  Kriterij CORRECTION_UP:
    1. Kvota raste (change_pct > 0.5%)
    2. Rast je RANI — >50% promjene u prvoj polovici praćenja
    3. Niska kasna koncentracija (late_conc < 0.3)
    4. Glatko kretanje (malo promjena smjera, dc_ratio < 0.25)
    5. Odds zona: 1.80 – 4.00
    6. Min. snapshots: 50

  CLI:
    python correction_tracker.py --scan              Prikaži signale + spremi CSV
    python correction_tracker.py --scan --min-odds 1.8 --max-odds 4.0
    python correction_tracker.py --watch             Pratiti svake 3 minute
    python correction_tracker.py --watch --interval 120
═══════════════════════════════════════════════════════════════════════════════
"""

import argparse
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ─── Windows encoding fix ───────────────────────────────────────────────────
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── Putanje ────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "odds_data" / "movement"       # cycle CSV fajlovi
OUTPUT_DIR  = BASE_DIR / "output" / "correction_up"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SIGNALS_FILE = OUTPUT_DIR / "signals.csv"               # Kumulativan log svih signala
TODAY_FILE   = OUTPUT_DIR / f"signals_{datetime.now().strftime('%Y-%m-%d')}.csv"

# ─── Parametri ──────────────────────────────────────────────────────────────
DEFAULT_MIN_ODDS    = 1.80
DEFAULT_MAX_ODDS    = 4.00
DEFAULT_MIN_SNAPS   = 50     # Min snapshots za pouzdanu klasifikaciju
DEFAULT_DAYS_BACK   = 2      # Koliko dana unazad učitati cycle CSV-ove

# CORRECTION_UP pragovi
CORRECTION_UP_LATE_CONC_MAX = 0.30   # Late koncentracija mora biti niska
CORRECTION_UP_DC_RATIO_MAX  = 0.25   # Malo promjena smjera
CORRECTION_UP_MIN_CHANGE_PCT = 0.5   # Min. % rast


# ═══════════════════════════════════════════════════════════════════════════
#  UČITAVANJE PODATAKA
# ═══════════════════════════════════════════════════════════════════════════
def load_cycle_data(days_back=DEFAULT_DAYS_BACK):
    """Učitaj cycle CSV fajlove iz zadnjih N dana."""
    now = datetime.now()
    csv_files = []
    for d in range(days_back, -1, -1):
        day = (now - timedelta(days=d)).strftime("%Y-%m-%d")
        csv_files.extend(sorted(DATA_DIR.glob(f"{day}*.csv")))

    if not csv_files:
        print(f"  [!] Nema CSV fajlova u: {DATA_DIR}")
        print(f"      Provjeri da je putanja točna.")
        return pd.DataFrame()

    frames = []
    for fp in csv_files:
        try:
            tmp = pd.read_csv(fp, sep=";", dtype=str)
            tmp["_source"] = fp.name
            frames.append(tmp)
        except Exception as e:
            continue

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")

    for col in ["odds_1", "odds_x", "odds_2"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["match_id"] = df["home"].astype(str).str.strip() + "|" + df["away"].astype(str).str.strip()
    return df


# ═══════════════════════════════════════════════════════════════════════════
#  TRAJEKTORIJA — izračun feature-a iz time-series snapshota
# ═══════════════════════════════════════════════════════════════════════════
def trajectory_features(vals):
    """
    Ulaz: numpy array kvota sortiranih po vremenu (za jedan market, jedan meč)
    Izlaz: dict s ključnim feature-ima
    """
    if len(vals) < 3:
        return dict(n_snaps=len(vals), n_changes=0, dc_ratio=0.0,
                    late_conc=0.5, rise_start="unknown",
                    change_pct=0.0, max_move_pct=0.0)

    changes = np.diff(vals)
    nonzero  = changes[changes != 0]

    n_changes = int(np.sum(changes != 0))
    n_snaps   = len(vals)

    # dc_ratio — udio promjena smjera
    if len(nonzero) > 1:
        signs = np.sign(nonzero)
        n_dir_changes = int(np.sum(signs[1:] != signs[:-1]))
        dc_ratio = n_dir_changes / n_changes if n_changes > 0 else 0.0
    else:
        dc_ratio = 0.0

    # late_conc — udio aps. promjene u zadnjih 30% snapshota
    total_abs = np.sum(np.abs(changes))
    if total_abs > 0:
        cutoff   = int(len(changes) * 0.7)
        late_abs = np.sum(np.abs(changes[cutoff:]))
        late_conc = float(late_abs / total_abs)
    else:
        late_conc = 0.5

    # rise_start_phase — "early" ako >50% promjene u prvoj polovici
    if total_abs > 0:
        mid = len(changes) // 2
        first_half_abs = np.sum(np.abs(changes[:mid]))
        rise_start = "early" if first_half_abs > 0.5 * total_abs else "late"
    else:
        rise_start = "unknown"

    open_val   = vals[0]
    change_pct = (vals[-1] - open_val) / open_val * 100 if open_val > 0 else 0.0
    max_move   = float(np.max(np.abs(changes)) / open_val * 100) if open_val > 0 else 0.0

    return dict(
        n_snaps=n_snaps,
        n_changes=n_changes,
        dc_ratio=dc_ratio,
        late_conc=late_conc,
        rise_start=rise_start,
        change_pct=change_pct,
        max_move_pct=max_move,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  CORRECTION_UP KLASIFIKATOR
# ═══════════════════════════════════════════════════════════════════════════
def is_correction_up(tf, close_odds, min_odds, max_odds):
    """
    Vrati True ako meč/market zadovoljava sve CORRECTION_UP kriterije.

    Kriteriji:
      - change_pct > min_change          (kvota raste)
      - rise_start == "early"            (rast počeo rano)
      - late_conc < 0.30                 (nema kasnog skoka)
      - dc_ratio < 0.25                  (glatko kretanje)
      - min_odds <= close_odds <= max_odds (odds zona)
      - n_snaps >= DEFAULT_MIN_SNAPS     (dovoljno snapshota)
    """
    if tf["change_pct"] <= CORRECTION_UP_MIN_CHANGE_PCT:
        return False, "NOT_RISING"
    if tf["n_snaps"] < DEFAULT_MIN_SNAPS:
        return False, "TOO_FEW_SNAPS"
    if tf["rise_start"] != "early":
        return False, f"RISE_LATE ({tf['rise_start']})"
    if tf["late_conc"] >= CORRECTION_UP_LATE_CONC_MAX:
        return False, f"LATE_CONC_HIGH ({tf['late_conc']:.2f})"
    if tf["dc_ratio"] >= CORRECTION_UP_DC_RATIO_MAX:
        return False, f"DC_RATIO_HIGH ({tf['dc_ratio']:.2f})"
    if not (min_odds <= close_odds <= max_odds):
        return False, f"ODDS_OUT_OF_RANGE ({close_odds:.2f})"
    return True, "CORRECTION_UP"


# ═══════════════════════════════════════════════════════════════════════════
#  GLAVNI SCAN
# ═══════════════════════════════════════════════════════════════════════════
def run_scan(min_odds=DEFAULT_MIN_ODDS, max_odds=DEFAULT_MAX_ODDS,
             days_back=DEFAULT_DAYS_BACK, quiet=False, save=True):
    """
    Učitaj podatke, pronađi CORRECTION_UP signale, prikaži i spremi u CSV.
    """
    now = datetime.now()

    if not quiet:
        print("═" * 65)
        print(f"  CORRECTION_UP TRACKER — {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Odds zona: {min_odds:.2f} – {max_odds:.2f} | Min snapshots: {DEFAULT_MIN_SNAPS}")
        print("═" * 65)

    # ── Učitaj podatke ──────────────────────────────────────────────────────
    df = load_cycle_data(days_back=days_back)
    if df.empty:
        if not quiet:
            print("  [!] Nema podataka za obradu.")
        return []

    if not quiet:
        n_files = df["_source"].nunique() if "_source" in df.columns else "?"
        print(f"  Učitano: {len(df):,} redaka iz {n_files} fajlova")

    # ── Filtriraj na upcoming ───────────────────────────────────────────────
    df["_status"] = df["status"].astype(str).str.strip().str.lower()
    upcoming_ids = df[df["_status"] == "upcoming"]["match_id"].unique()

    # Isključi par koji je već završen (ghost match filter)
    finished_pairs = set(df[df["_status"] == "finished"]["match_id"].unique())
    upcoming_ids = [mid for mid in upcoming_ids if mid not in finished_pairs]

    if not quiet:
        print(f"  Upcoming mečeva (čisti): {len(upcoming_ids)}")

    if len(upcoming_ids) == 0:
        if not quiet:
            print("  Nema upcoming mečeva.")
        return []

    # ── Izračunaj trajektoriju po meču × marketu ────────────────────────────
    df_sorted = df.sort_values(["match_id", "scraped_at"])
    signals = []

    markets = [("1", "odds_1"), ("x", "odds_x"), ("2", "odds_2")]

    for mid in upcoming_ids:
        grp = df_sorted[df_sorted["match_id"] == mid]
        last_row = grp.iloc[-1]

        home    = str(last_row.get("home", "?")).strip()
        away    = str(last_row.get("away", "?")).strip()
        ko      = str(last_row.get("kick_off", "?")).strip()
        league  = str(last_row.get("league", "?")).strip()
        country = str(last_row.get("country", "?")).strip()

        for suf, col in markets:
            vals = grp[col].dropna().values
            if len(vals) < 3:
                continue

            close_odds = float(vals[-1])
            if close_odds <= 1.0:
                continue

            tf = trajectory_features(vals)
            ok, reason = is_correction_up(tf, close_odds, min_odds, max_odds)

            if not ok:
                continue

            out_label = {"1": "1", "x": "X", "2": "2"}[suf]
            open_odds = float(vals[0])

            signals.append({
                "scan_time":    now.strftime("%Y-%m-%d %H:%M:%S"),
                "kick_off":     ko,
                "home":         home,
                "away":         away,
                "league":       league,
                "country":      country,
                "tip":          out_label,
                "odds_open":    round(open_odds, 2),
                "odds_close":   round(close_odds, 2),
                "change_pct":   round(tf["change_pct"], 2),
                "late_conc":    round(tf["late_conc"], 3),
                "dc_ratio":     round(tf["dc_ratio"], 3),
                "rise_start":   tf["rise_start"],
                "n_snaps":      tf["n_snaps"],
                "n_changes":    tf["n_changes"],
                "max_move_pct": round(tf["max_move_pct"], 2),
                "signal":       "CORRECTION_UP",
                "status":       "pending",
                "result":       "",
                "profit_loss":  "",
                "notes":        "",
            })

    # ── Prikaz ─────────────────────────────────────────────────────────────
    if not quiet:
        print(f"\n  Pronađeno CORRECTION_UP signala: {len(signals)}")
        if signals:
            print()
            print(f"  {'#':>3}  {'KO':>5}  {'Home':<22} {'Away':<22} {'T':>2} {'Odds':>6}  {'Chg%':>6}  {'Late':>5}  {'DC':>5}")
            print("  " + "─" * 80)
            for i, s in enumerate(signals, 1):
                print(f"  {i:>3}  {s['kick_off']:>5}  {s['home']:<22.22} {s['away']:<22.22}"
                      f"  {s['tip']:>2}  {s['odds_close']:>6.2f}  {s['change_pct']:>5.1f}%"
                      f"  {s['late_conc']:>5.3f}  {s['dc_ratio']:>5.3f}")
        else:
            print("  Nema signala trenutno.")

    # ── Spremi u CSV ────────────────────────────────────────────────────────
    if save and signals:
        sig_df = pd.DataFrame(signals)

        # Dnevni fajl
        today_path = OUTPUT_DIR / f"signals_{now.strftime('%Y-%m-%d')}.csv"
        sig_df.to_csv(today_path, index=False, sep=";")

        # Kumulativan log (append)
        if SIGNALS_FILE.exists():
            existing = pd.read_csv(SIGNALS_FILE, sep=";", dtype=str)
            # Deduplikacija: ne dodaj isti meč/market ako već postoji u logu danas
            existing["_key"] = existing["scan_time"].str[:10] + "|" + existing["home"] + "|" + existing["away"] + "|" + existing["tip"]
            sig_df["_key"]   = sig_df["scan_time"].str[:10] + "|" + sig_df["home"] + "|" + sig_df["away"] + "|" + sig_df["tip"]
            new_rows = sig_df[~sig_df["_key"].isin(existing["_key"])].drop(columns=["_key"])
            if len(new_rows) > 0:
                new_rows.to_csv(SIGNALS_FILE, mode="a", header=False, index=False, sep=";")
                if not quiet:
                    print(f"\n  Novi unosi dodani u log: {len(new_rows)}")
            else:
                if not quiet:
                    print(f"\n  Nema novih signala za log (sve već zabilježeno).")
        else:
            sig_df.drop(columns=["_key"], errors="ignore").to_csv(SIGNALS_FILE, index=False, sep=";")

        if not quiet:
            print(f"  Dnevni fajl:    {today_path}")
            print(f"  Kumulativan log: {SIGNALS_FILE}")

    return signals


# ═══════════════════════════════════════════════════════════════════════════
#  WATCH MODE — automatski scan svake N sekundi
# ═══════════════════════════════════════════════════════════════════════════
def run_watch(min_odds=DEFAULT_MIN_ODDS, max_odds=DEFAULT_MAX_ODDS, interval=180):
    """Kontinuirano skeniranje, svake `interval` sekundi."""
    print("═" * 65)
    print(f"  WATCH MODE — CORRECTION_UP tracker")
    print(f"  Interval: svake {interval}s | Zaustavi: Ctrl+C")
    print("═" * 65)

    seen = set()  # Set ključeva već viđenih signala
    cycle = 0

    try:
        while True:
            cycle += 1
            now = datetime.now()
            print(f"\n  ── Ciklus {cycle} — {now.strftime('%H:%M:%S')} ──")

            signals = run_scan(min_odds=min_odds, max_odds=max_odds, quiet=True, save=True)

            new = []
            for s in signals:
                key = f"{s['home']}|{s['away']}|{s['tip']}|{now.strftime('%Y-%m-%d')}"
                if key not in seen:
                    seen.add(key)
                    new.append(s)

            if new:
                print(f"  🆕 {len(new)} NOVI CORRECTION_UP signal(i):")
                for s in new:
                    print(f"     {s['kick_off']}  {s['home']} vs {s['away']}"
                          f"  |  {s['tip']} @ {s['odds_close']:.2f}"
                          f"  |  chg={s['change_pct']:.1f}%  late={s['late_conc']:.2f}  dc={s['dc_ratio']:.2f}")
            else:
                total = len(signals)
                print(f"  Nema novih signala ({total} ukupno praćeno, {len(seen)} zabilježeno)")

            next_scan = (now + timedelta(seconds=interval)).strftime("%H:%M:%S")
            print(f"  Sljedeći scan: {next_scan}")
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n  Watch mode zaustavljen. Ukupno praćeno: {len(seen)} signala.")


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="CORRECTION_UP Tracker — prati samo CORRECTION_UP signale",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Primjeri:
  python correction_tracker.py --scan
  python correction_tracker.py --scan --min-odds 2.0 --max-odds 3.5
  python correction_tracker.py --watch
  python correction_tracker.py --watch --interval 120
        """
    )
    parser.add_argument("--scan",      action="store_true", help="Jednokratni scan i spremi CSV")
    parser.add_argument("--watch",     action="store_true", help="Kontinuirani scan (Ctrl+C za stop)")
    parser.add_argument("--min-odds",  type=float, default=DEFAULT_MIN_ODDS, help=f"Min odds (default: {DEFAULT_MIN_ODDS})")
    parser.add_argument("--max-odds",  type=float, default=DEFAULT_MAX_ODDS, help=f"Max odds (default: {DEFAULT_MAX_ODDS})")
    parser.add_argument("--interval",  type=int,   default=180, help="Watch interval u sekundama (default: 180)")
    parser.add_argument("--days-back", type=int,   default=DEFAULT_DAYS_BACK, help=f"Koliko dana unazad (default: {DEFAULT_DAYS_BACK})")

    args = parser.parse_args()

    if not args.scan and not args.watch:
        parser.print_help()
        return

    if args.scan:
        run_scan(min_odds=args.min_odds, max_odds=args.max_odds,
                 days_back=args.days_back)

    if args.watch:
        run_watch(min_odds=args.min_odds, max_odds=args.max_odds,
                  interval=args.interval)


if __name__ == "__main__":
    main()