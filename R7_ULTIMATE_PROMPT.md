# ULTIMATIVNI PROMPT: Zamjena Composite Score sustava s R7 filterom u trader.py

---

## 1. CILJ

Imam datoteku `trader.py` (1271 linija) koja koristi **Composite Score** sustav za generiranje paper trading signala iz kretanja kladioničarskih kvota. Composite Score sustav **nije profitabilan**.

U zasebnom backtesting projektu testirao sam mnogo strategija i **jedina profitabilna je R7**:

| Metrika | Composite Score (staro) | R7 (novo) |
|---------|------------------------|-----------|
| N oklada | 238 | 136 |
| Accuracy | 45.8% | **44.9%** |
| ROI | +27.7% | **+28.2%** |
| Profit | — | **+740€** (na bankroll 1000€) |
| MaxDD | 18.7% | 24.9% |
| p-value | <0.0001 | **0.0034** |
| Profitabilan | ❌ NE | ✅ DA |

**Zadatak: Zamijeni Composite Score sustav s R7 filterom u `trader.py`. Zadrži svu ostalu infrastrukturu (CLI, paper trades, update, report, weekly, watch) — samo zamijeni scoring/filtering logiku.**

---

## 2. ARHITEKTURA PROMJENA

```
STARO                              NOVO
─────────────────────────          ─────────────────────────
compute_features()                 compute_features()  ← PROŠIRITI
  → pct_change, mono, vol           → DODATI: n_changes, n_direction_changes,
                                       max_single_move, late_conc, rise_start_phase,
                                       change_pct (alias)

classify_signal()                  classify_rising_type()  ← ZAMIJENITI
  → 5 tipova (pct + mono)           → 7 tipova (trajectory features)

calculate_composite_score()        compute_quality_score()  ← ZAMIJENITI
  → 0-80+ bodova                    → 0-100 bodova

run_scan():                        run_scan():
  filter: score >= 50                filter: quality≥70 AND odds 1.80-4.00
  dual account A(≥50)/B(≥55)              AND non_exotic AND good_type
                                     single R7 account
                                     + ghost match filtering
                                     + med_speed/med_counter computation
```

---

## 3. FORMAT ULAZNIH CSV PODATAKA

Datoteke su u `odds_data/movement/`, nazvane `YYYY-MM-DD_HHMMSS_cycleN.csv`.
Separator: `;`

**Kolone:**
```
scraped_at;match_date;kick_off;country;league;home;away;odds_1;odds_x;odds_2;score;status
```

**Primjer:**
```
2026-02-24T10:37:27.784207;2026-02-24;00:00;Brazil;Acreano;Rio Branco;Santa Cruz AC;3.63;3.51;2.3;0-3;finished
2026-02-24T10:37:27.784207;2026-02-25;15:00;England;Premier League;Arsenal;Chelsea;;;3.10;;upcoming
```

- `status`: "upcoming" ili "finished"
- `score`: format "H-A" (npr "2-1") za finished, prazan za upcoming
- `match_id` se generira kao: `home|away` (BEZ datuma)
- Svaki cycle CSV sadrži snapshot svih utakmica u tom trenutku
- Utakmica ima ~20-60 snapshota kroz ~6-24h monitoringa

---

## 4. KOMPLETNI TRENUTNI `trader.py` KOD

Ovo je **čitav** trenutni kod koji trebaš modificirati. Čitaj ga pažljivo da razumiješ strukturu prije nego počneš mijenjati.

```python
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
    'max_drawdown_pct': 25,
    'pause_drawdown_pct': 15,
    'min_accuracy_50bets': 35,
    'max_consecutive_losses': 8,
    'negative_weeks': 3,
    'min_bets_for_eval': 50,
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
```

---

## 5. FUNKCIJE KOJE TREBA ZADRŽATI BEZ PROMJENA

Ove funkcije NE DIRAJ — rade savršeno kako jesu:

- `load_movement_data(days_back=2)` — učitava CSV-ove, spaja, parsira
- `kelly_stake(odds, win_prob, fraction=0.25)` — Kelly criterion
- `load_trades()` / `save_trades(trades)` / `get_next_trade_id(trades)` — trade I/O
- `_recalc_cumulative(trades, account)` — PnL recalc
- `check_stop_rules(trades, account)` — stop/pause provjera
- `run_update()` — ažurira rezultate završenih mečeva
- `run_report()` — dnevni report (**ali prilagodi za single account**)
- `run_weekly()` — tjedna validacija (**ali prilagodi za single account**)
- `run_watch()` — watch mode
- `main()` — CLI argumenti

---

## 6. NOVE FUNKCIJE — KOMPLETNI KOD

### 6.1. `compute_trajectory_features(vals)` — NOVA helper funkcija

Ova funkcija prima niz odds vrijednosti za JEDAN market jedne utakmice i računa trajectory feature-e.

```python
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
        cutoff = int(len(changes) * 0.7)  # granica za "zadnjih 30%"
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
```

### 6.2. `compute_features(df)` — PROŠIRENA verzija

Zadrži sve postojeće feature-e (`open_1/x/2`, `close_1/x/2`, `pct_change_1/x/2`, `mono_1/x/2`, `vol_1/x/2`, `smooth_rise_1/x/2`, `overround_change`, `n_snapshots`, `mon_hours`).

**DODAJ** ove nove feature-e za svaki market (1, x, 2):
- `n_changes_{side}`
- `n_direction_changes_{side}`
- `max_single_move_{side}`
- `late_conc_{side}`
- `rise_start_phase_{side}`
- `change_pct_{side}` — ALIAS za `pct_change_{side}` (R7 koristi `change_pct_` prefiks)

**Unutar `traj_features()` pod-funkcije**, dodaj poziv `compute_trajectory_features()`:

```python
def traj_features(grp):
    result = {}
    for market, col in [("1", "odds_1"), ("X", "odds_x"), ("2", "odds_2")]:
        side = market.replace("X", "x")
        vals = grp[col].values
        
        # POSTOJEĆI feature-i (zadrži)
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
        
        # NOVI trajectory feature-i za R7
        tf = compute_trajectory_features(vals)
        result[f"n_changes_{side}"] = tf["n_changes"]
        result[f"n_direction_changes_{side}"] = tf["n_direction_changes"]
        result[f"max_single_move_{side}"] = tf["max_single_move_pct"]
        result[f"late_conc_{side}"] = tf["late_conc"]
        result[f"rise_start_phase_{side}"] = tf["rise_start_phase"]
    
    return pd.Series(result)
```

Nakon joinanja feature-a, dodaj **alias** `change_pct_*` koji je jednak `pct_change_*`:
```python
# Nakon "for side in ["1", "x", "2"]:" bloka koji računa pct_change:
for side in ["1", "x", "2"]:
    m[f"change_pct_{side}"] = m[f"pct_change_{side}"]  # alias za R7
```

### 6.3. `classify_rising_type(row, suffix)` — ZAMJENA za `classify_signal()`

```python
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
```

### 6.4. `compute_quality_score(row, suffix, league_roi_map, med_speed, med_counter)` — ZAMJENA za `calculate_composite_score()`

```python
def compute_quality_score(row, suffix, league_roi_map, med_speed, med_counter):
    """
    0–100 quality score za RISING outcome.
    R7 filtira quality >= 70.
    
    BONUSI (max 100):
      +20  smoothness > 0.7    (glatki rast bez puno smjenа smjera)
      +15  speed < med_speed   (sporiji rast = stabilniji)
      +15  |overround_change| < 1.0   (overround stabilan)
      +15  counter < med_counter  (suprotni market ne reagira snažno)
      +10  late_conc < 0.5     (promjena nije koncentrirana na kraju)
      +10  close_odds >= 2.0   (nije favorit)
      +10  league ROI > 0      (liga historijski profitabilna)
      +5   1.80 <= odds <= 4.00  (sweet-spot kvote)
    
    PENALI:
      -20  smoothness < 0.3    (previše neravnomjerno)
      -20  |overround_change| > 3.0  (overround drastično nestabilan)
      -15  late_conc > 0.80    (previše kasna promjena)
    
    Args:
        row: dict/Series s feature-ima
        suffix: "1", "x", ili "2"
        league_roi_map: dict {league_name: roi_pct} — može biti {} za početak
        med_speed: medijan brzine (change_pct / n_snapshots) iz finished rising matches
        med_counter: medijan kontra-kretanja suprotnog marketa iz finished
    """
    score = 0
    cp   = abs(row.get(f"change_pct_{suffix}", 0) or 0)
    nc   = max(row.get(f"n_changes_{suffix}", 0) or 0, 1)
    ndc  = row.get(f"n_direction_changes_{suffix}", 0) or 0
    ns   = max(row.get("n_snapshots", 1) or 1, 1)
    lc   = row.get(f"late_conc_{suffix}", 0.5)
    orc  = abs(row.get("overround_change", 0) or 0)
    close_odds = row.get(f"close_{suffix}", 3.0) or 3.0

    smoothness = 1 - (ndc / nc)       # 0-1, viši = glađi
    speed      = cp / max(ns, 1)      # % promjene po snapshotu

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
```

### 6.5. `compute_reference_medians(m)` — NOVA funkcija

Računa `med_speed` i `med_counter` iz finished utakmica:

```python
def compute_reference_medians(m):
    """
    Compute median speed and median counter-movement from finished matches.
    These are reference values for quality scoring.
    
    Args:
        m: DataFrame with all matches (including finished), with columns:
           change_pct_1/x/2, n_snapshots
    
    Returns:
        (med_speed, med_counter) — tuple of floats
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
```

---

## 7. GHOST MATCH FILTERING

**Problem:** match_id je `home|away` bez datuma. Ista utakmica (npr. Manisa FK vs Bandirmaspor) može se pojaviti kao "finished" u jednom ciklusu i "upcoming" u kasnijem ciklusu jer se isti parovi igraju u različitim kolima. Ali data scraper ponekad pokupi staru finished utakmicu i novu upcoming utakmicu istih timova istovremeno.

**Rješenje:** Prikupi SVE team-pair-ove koji su ikad imali status "finished" i isključi ih iz upcoming signala.

```python
def filter_ghost_matches(df, upcoming_df):
    """
    Remove upcoming matches whose team pair has ever been seen as 'finished'.
    
    Args:
        df: full DataFrame (all cycles, all statuses)
        upcoming_df: DataFrame of upcoming matches only
    
    Returns:
        filtered upcoming DataFrame
    """
    # Svi parovi koji su ikad bili finished
    finished_mask = df["status"].astype(str).str.strip().str.lower() == "finished"
    finished_pairs = set()
    for _, row in df[finished_mask].iterrows():
        pair = f"{str(row['home']).strip()}|{str(row['away']).strip()}"
        finished_pairs.add(pair)
    
    # Filtriraj upcoming
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
```

---

## 8. MODIFICIRANA `run_scan()` — KOMPLETNA NOVA VERZIJA

Ovo je srce promjene. Zamijeni čitavu `run_scan()` funkciju:

```python
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
    
    # League ROI map (prazan za početak; implementirati kad bude dovoljno podataka)
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
                "market": suffix.upper().replace("X", "X"),
                "predicted_outcome": predicted,
                "score": quality,   # R7 quality score (0-100)
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
```

---

## 9. MODIFICIRANA `record_signals_as_trades()` — SINGLE ACCOUNT

Umjesto dual account (A: ≥50, B: ≥55), sada koristimo **jedan R7 account**:

```python
def record_signals_as_trades(signals):
    """Record all R7 signals as paper trades (single account)."""
    new_count = 0
    for sig in signals:
        t = record_trade(sig, account="R7")
        if t:
            new_count += 1
    print(f"  Recorded: {new_count} new R7 trades")
```

U `record_trade()` funkciji, promijeni default account na "R7":
```python
def record_trade(signal, account="R7"):
```

---

## 10. PRILAGODBE U `run_report()` i `run_weekly()`

Umjesto iteriranja po accountima ["A", "B"], iteriraj po ["R7"]:

```python
# STARO:
for account in ["A", "B"]:
    threshold_label = "≥50" if account == "A" else "≥55"

# NOVO:
for account in ["R7"]:
    threshold_label = "Q≥70"
```

U `run_report()`:
- Ukloni A/B comparison sekciju (više nema dva accounta)
- Zamijeni CORRECTION_UP MONITORING s R7 TYPE MONITORING (prijaži accuracy po signal_type)

U `run_weekly()`:
- Promijeni `expected = 0.458` na `expected = 0.449` (R7 backtest accuracy)
- Iteriraj po `["R7"]` umjesto `["A", "B"]`

---

## 11. PRILAGODBE U `run_watch()`

```python
# STARO:
def run_watch(threshold=50, interval=180):

# NOVO:
def run_watch(threshold=70, interval=180):
```

I u CLI:
```python
parser.add_argument("--threshold", type=int, default=70, help="Minimum R7 quality (default: 70)")
```

---

## 12. TRADES_FIELDS — dodaj quality-specific polja

```python
TRADES_FIELDS = [
    "trade_id", "date", "match_id", "home", "away", "league", "country",
    "predicted_outcome", "score_composite", "signal_type", "odds_at_signal",
    "stake_pct", "stake_eur", "status", "actual_result", "actual_score",
    "profit_loss", "cumulative_pnl", "account", "notes",
]
```

`score_composite` sad sadrži R7 quality (0-100). Možeš ga preimenovati u `quality_score` ako želiš, ali onda trebaš migrirati stare trades. Lakše je zadržati isto ime polja i samo staviti novu vrijednost.

---

## 13. KOMPLETNA REFERENTNA IMPLEMENTACIJA

Ovo je radna implementacija R7 filtera iz mog backtesting projekta (`_upcoming.py`). Koristi je kao referencu da provjeriš da su logika i redoslijed operacija ispravni:

```python
import pickle, numpy as np, pandas as pd

R7_TYPES = {"DRIFT_UP", "CORRECTION_UP", "NEGLECT_UP"}


def classify_rising_type(row, suffix):
    cp  = row.get(f"change_pct_{suffix}", 0) or 0
    nc  = row.get(f"n_changes_{suffix}", 0) or 0
    ndc = row.get(f"n_direction_changes_{suffix}", 0) or 0
    msm = row.get(f"max_single_move_{suffix}", 0) or 0
    lc  = row.get(f"late_conc_{suffix}", 0.5)
    ns  = row.get("n_snapshots", 0) or 0

    if cp <= 0.5:
        return "NOT_RISING"
    dc_ratio = ndc / nc if nc > 0 else 0
    if lc > 0.80 and cp > 3:
        return "LATE_SPIKE_UP"
    if msm > 0 and cp > 0 and (msm / cp) > 0.60 and cp > 3:
        return "STEAM_UP"
    if cp > 15:
        return "OVERREACTION_UP"
    if dc_ratio > 0.40 and cp > 1:
        return "BOUNCE_UP"
    changes_ratio = nc / ns if ns > 0 else 1
    if changes_ratio < 0.15 and cp > 1:
        return "NEGLECT_UP"
    rsp = row.get(f"rise_start_phase_{suffix}", "unknown")
    if rsp == "early" and lc < 0.3 and dc_ratio < 0.25:
        return "CORRECTION_UP"
    if dc_ratio < 0.25:
        return "DRIFT_UP"
    return "DRIFT_UP"


def compute_quality_score(row, suffix, league_roi_map, med_speed, med_counter):
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

    opposites = {"1": "2", "2": "1", "x": "1"}
    opp = opposites.get(suffix, "2")
    counter = abs(row.get(f"change_pct_{opp}", 0) or 0)
    is_fav = close_odds < 2.0

    if smoothness > 0.7:   score += 20
    if speed < med_speed:  score += 15
    if orc < 1.0:          score += 15
    if counter < med_counter: score += 15
    if lc < 0.5:           score += 10
    if not is_fav:         score += 10
    lg = row.get("league", "")
    if league_roi_map.get(lg, -1) > 0: score += 10
    try:
        co = float(close_odds)
        if 1.80 <= co <= 4.00: score += 5
    except Exception:
        pass

    if smoothness < 0.3:   score -= 20
    if orc > 3.0:          score -= 20
    if lc > 0.80:          score -= 15

    return max(0, min(100, score))
```

---

## 14. EDGE CASES I VAŽNE NAPOMENE

### 14.1. Bet strana — KONTRARIAN
Kada kvota za HOME (odds_1) RASTE, to znači da tržište smatra HOME manje vjerojatnijim. Ali R7 betta upravo NA tu rising stranu — to je kontrarian pristup. Dakle:
- Kvota za "1" raste → kvalificira se → bet na **HOME**
- Kvota za "x" raste → kvalificira se → bet na **DRAW**
- Kvota za "2" raste → kvalificira se → bet na **AWAY**

### 14.2. Market X (remi)
R7 uključuje i remi. Provjeravaj sva tri marketa (1, X, 2), ne skipi X.

### 14.3. Dedup
Jedna oklada po utakmici — ako dva marketa na istoj utakmici prolaze R7 filter, zadrži onaj s najvišim quality score-om.

### 14.4. Staking
Flat 2% bankrolla po okladi = €20 za bankroll €1000.

### 14.5. R7 NIJE ML model
Čisti rule-based filter. Nema treniranja, nema modela, nema pickle fajlova. Samo pravila na feature-ima.

### 14.6. `change_pct` vs `pct_change`
Backtesting projekt koristi `change_pct_{suffix}`, DANAS koristi `pct_change_{suffix}`. Dodaj alias:
```python
m[f"change_pct_{side}"] = m[f"pct_change_{side}"]
```

### 14.7. `overround_change` skala
U backtesting projektu, `overround_change` je u postotnim bodovima (npr. 0.5 znači 0.5pp promjena). U DANAS je izračunat kao razlika probability-space (npr. close_overround - open_overround, oboje su frakcije ~1.05). Pronormaliziraj na istu skalu: pomnoži s 100 ako je frakcija, ili koristi apsolutnu razliku × 100.

Provjeri: u trenutnom DANAS kodu:
```python
m["overround_change"] = m["close_overround"] - m["open_overround"]
```
Ovo daje male brojeve (~0.01-0.05). Kvaliteta provjera je `orc < 1.0` i `orc > 3.0`, što je kalibrirano za postotne bodove. Dakle pretvori:
```python
m["overround_change"] = (m["close_overround"] - m["open_overround"]) * 100
```

### 14.8. Fallback med_speed/med_counter
Ako nema dovoljno finished podataka (npr. prvi dan korištenja):
- `med_speed = 0.5`
- `med_counter = 3.0`

### 14.9. `league_roi_map`
Za početak koristi prazan dict `{}`. Tada quality score gubi max 10 bodova za tu komponentu. R7 filter i dalje radi — samo sustav neće davati bonus za profitabilne lige. Kasnije se može implementirati iz vlastitih paper trade rezultata.

### 14.10. Kompatibilnost starih trades
Stari paper trades (iz Composite Score perioda) imaju `account` = "A" ili "B". Novi imaju "R7". `run_report()` i `run_weekly()` trebaju iterirati po `["R7"]`. Stari trades ostaju u CSV-u ali se neće prikazivati u novim reportima (osim ako dodaš legacy prikaz).

---

## 15. PRIMJER OČEKIVANOG OUTPUTA

```
═══════════════════════════════════════════════════════════════════════════
  R7 SCANNER — 2026-02-24 10:30:00
  Filter: quality≥70, odds 1.80-4.00, non-exotic, rising type OK
═══════════════════════════════════════════════════════════════════════════
  Loaded 5,432 rows from 45 files
  Reference: med_speed=0.431, med_counter=2.876
  Upcoming matches: 128
  Ghost filter: 128 → 94 (removed 34 ghost matches)
  After ghost filter: 94

  R7 SIGNALS: 3
──────────────────────────────────────────────────────────────────────────

  [Q90] HSV vs RB Leipzig | HOME @ 3.50 | DRIFT_UP
  League: 2. Bundesliga (Germany)
  Breakdown: smooth(+20) slow(+15) orc_ok(+15) weak_ctr(+15) not_late(+10) not_fav(+10) sweet(+5)
  Change: +4.2% | Snapshots: 42 | Overround: 104.5%→104.2%
  Kelly: 2.8% | Suggested: 2% flat = €20

  [Q75] Cadiz vs Leganes | AWAY @ 2.60 | CORRECTION_UP
  League: LaLiga2 (Spain)
  Breakdown: smooth(+20) slow(+15) orc_ok(+15) not_late(+10) not_fav(+10) sweet(+5)
  Change: +3.5% | Snapshots: 35 | Overround: 105.1%→104.8%
  Kelly: 1.9% | Suggested: 2% flat = €20

  [Q70] Roma vs Juventus | AWAY @ 2.85 | NEGLECT_UP
  League: Serie A (Italy)
  Breakdown: smooth(+20) orc_ok(+15) not_late(+10) not_fav(+10) league_roi(+10) sweet(+5)
  Change: +3.1% | Snapshots: 38 | Overround: 103.8%→103.5%
  Kelly: 2.1% | Suggested: 2% flat = €20

──────────────────────────────────────────────────────────────────────────
  Found 3 R7 signals
  Alerts saved: output/alerts/2026-02-24_1030.csv
  Recorded: 3 new R7 trades
```

---

## 16. KONTROLNA LISTA — provjeriti nakon implementacije

- [ ] `compute_trajectory_features()` je dodana kao nova helper funkcija
- [ ] `compute_features()` sad računa `n_changes`, `n_direction_changes`, `max_single_move`, `late_conc`, `rise_start_phase` za svaki market
- [ ] `change_pct_{side}` alias postoji (= `pct_change_{side}`)
- [ ] `overround_change` je u postotnim bodovima (× 100)
- [ ] `classify_rising_type()` zamijenila staru `classify_signal()`
- [ ] `compute_quality_score()` zamijenila staru `calculate_composite_score()`
- [ ] `compute_reference_medians()` je dodana
- [ ] `filter_ghost_matches()` je dodana
- [ ] `run_scan()` koristi R7 filter (4 uvjeta)
- [ ] `run_scan()` provjerava sva 3 marketa (1, X, 2) — ne skippa draw
- [ ] Dedup po match_id (jedan signal po utakmici, najviši quality)
- [ ] Default threshold = 70 (ne 50)
- [ ] `record_signals_as_trades()` koristi single "R7" account
- [ ] `record_trade()` default account = "R7"
- [ ] `run_report()` iterira po ["R7"]
- [ ] `run_weekly()` iterira po ["R7"] i koristi expected = 0.449
- [ ] `run_watch()` default threshold = 70
- [ ] CLI `--threshold` default = 70
- [ ] `est_winprob = 0.449` za R7
- [ ] Docstring na vrhu fajla ažuriran za R7
- [ ] `kelly_stake()`, `load_trades()`, `save_trades()`, `run_update()` NISU dirnuti
- [ ] `EXOTIC_COUNTRIES` set je prisutan
- [ ] `R7_ACCEPTED_TYPES = {"DRIFT_UP", "CORRECTION_UP", "NEGLECT_UP"}` je definiran

---

## 17. SAŽETAK SVIH PROMJENA

| # | Što | Akcija | Detalji |
|---|-----|--------|---------|
| 1 | Docstring | EDIT | Promijeni opis na R7 |
| 2 | `R7_ACCEPTED_TYPES` | ADD | Nova konstanta |
| 3 | `compute_trajectory_features()` | ADD | Nova helper funkcija |
| 4 | `compute_features()` traj_features | EDIT | Dodaj trajectory feature-e |
| 5 | `compute_features()` alias | ADD | `change_pct_*` = `pct_change_*` |
| 6 | `compute_features()` overround | EDIT | × 100 za postotne bodove |
| 7 | `classify_signal()` | REPLACE | Nova `classify_rising_type()` |
| 8 | `calculate_composite_score()` | REPLACE | Nova `compute_quality_score()` |
| 9 | `compute_reference_medians()` | ADD | Nova funkcija |
| 10 | `filter_ghost_matches()` | ADD | Nova funkcija |
| 11 | `run_scan()` | REWRITE | R7 filter, ghost, medians, display |
| 12 | `record_signals_as_trades()` | EDIT | Single R7 account |
| 13 | `record_trade()` | EDIT | Default account="R7" |
| 14 | `run_report()` | EDIT | ["R7"] umjesto ["A","B"] |
| 15 | `run_weekly()` | EDIT | ["R7"], expected=0.449 |
| 16 | `run_watch()` | EDIT | threshold=70 |
| 17 | CLI `--threshold` | EDIT | default=70 |
| 18 | Stare funkcije | DELETE | `classify_signal()`, `calculate_composite_score()` |

---

## 18. TESTIRANJE

Nakon implementacije, pokreni:
```bash
python trader.py --scan
```

Očekivani rezultat:
- Ispiše "R7 SCANNER" header
- Prikaže med_speed i med_counter
- Prikaže ghost filter statistiku
- Lista 0-10 R7 signala (quality≥70) s breakdown-om
- Spremi alerts CSV

```bash
python trader.py --scan --record
```
- Isto kao gore + snima trades u trades.csv s account="R7"

```bash
python trader.py --report
```
- Prikaže dnevni report za account R7

```bash
python trader.py --update
```
- Ažurira završene mečeve (ova funkcija je nepromijenjena)
