# PROMPT ZA IMPLEMENTACIJU R7 FILTERA U DANAS PROJEKT

---

## KONTEKST

Imam projekt za praćenje kretanja kladioničarskih kvota u direktoriju `c:\Users\bibia\Desktop\DANAS\`. Sustav skenira kvote s OddsPortala svakih ~3 minute, sprema ih u CSV datoteke (u `odds_data/movement/`), i trenutno koristi **Composite Score** sustav za generiranje signala.

Composite Score sustav je backtestiran ali nije profitabilan. Imam zasebni backtesting projekt (`ANALIZAMOVMENTA23`) u kojem sam testirao mnogo strategija, i **jedina profitabilna strategija je R7**:

**R7 Backtest rezultati:**
- N = 136 oklada
- Accuracy = 44.9%
- ROI = **+28.2%**
- Profit = **+740€** (bankroll 1000€)
- MaxDD = 24.9%
- p-value = 0.0034 (statistički signifikantno)

Sada želim **zamijeniti Composite Score sustav s R7 filterom** u svom DANAS projektu.

---

## TRENUTNA STRUKTURA DANAS PROJEKTA

### Glavni fajl: `trader.py` (1271 linija)

**CLI naredbe:**
```
python trader.py --scan                 # Skenira upcoming utakmice, prikazuje signale
python trader.py --scan --record        # Skenira + automatski zapisuje paper trades
python trader.py --update               # Ažurira rezultate završenih utakmica
python trader.py --report               # Dnevni dashboard
python trader.py --weekly               # Tjedna validacija
python trader.py --watch                # Kontinuirani monitoring (svake 3 min)
```

### Format CSV podataka (odds_data/movement/)
Datoteke su nazvane: `YYYY-MM-DD_HHMMSS_cycleN.csv`
Separator: `;`
Kolone:
```
scraped_at;match_date;kick_off;country;league;home;away;odds_1;odds_x;odds_2;score;status
```

Primjer reda:
```
2026-02-24T10:37:27.784207;2026-02-24;00:00;Brazil;Acreano;Rio Branco;Santa Cruz AC;3.63;3.51;2.3;0-3;finished
```

- `status` je "upcoming" ili "finished"
- `score` je "H-A" format (npr "2-1") za završene, prazan za upcoming
- `match_id` se generira kao `home|away` (bez datuma!)

### Trenutna `compute_features()` funkcija računa:
- `open_1/x/2`, `close_1/x/2` — prvi i zadnji snap odds
- `pct_change_1/x/2` — postotak promjene kvote
- `mono_1/x/2` — monotonost (koliko je promjena jednosmjerna)
- `vol_1/x/2` — volatilnost (std dev promjena)
- `smooth_rise_1/x/2` — bool, mono≥0.7 i pct<-3%
- `open_overround`, `close_overround`, `overround_change`
- `n_snapshots`
- `mon_hours` — sati monitoringa

### Trenutna `classify_signal()` je JEDNOSTAVNA — koristi samo pct_change i mono:
- CORRECTION_UP: pct > 3%
- NEGLECT_UP: pct > 5% i mono ≥ 0.5
- DRIFT_UP: pct > 0
- STEAM_DOWN: pct < -8% i mono ≥ 0.7

### Trenutni `calculate_composite_score()` daje 0-80 bodova na temelju:
- Signal type bonus (max 30)
- Smooth rise (+10)
- Odds sweet spot (+10)
- League quality (+10/-5)
- Data quality (+5/-10)
- Overround change (+5)

**OVO SVE TREBA ZAMIJENITI S R7 FILTEROM.**

---

## R7 FILTER — KOMPLETNA SPECIFIKACIJA

### R7 ima 4 uvjeta (SVI moraju biti ispunjeni):

```python
quality >= 70 AND
1.80 <= close_odds <= 4.00 AND
league in non_exotic AND
rising_type in ("DRIFT_UP", "CORRECTION_UP", "NEGLECT_UP")
```

### Feature-i koji NEDOSTAJU u trenutnom kodu (moraju se dodati u compute_features):

Za svaki market (1, X, 2) trebam izračunati:

1. **`n_changes_{suffix}`** — broj snapshot-ova u kojima se kvota **stvarno promijenila** (≠ prethodni)
2. **`n_direction_changes_{suffix}`** — broj puta kad se smjer promjene **okrenuo** (rasla→padala ili obrnuto)
3. **`max_single_move_{suffix}`** — najveća apsolutna promjena između dva uzastopna snapshota (u % od open)
4. **`late_conc_{suffix}`** (late concentration) — udio ukupne promjene koji se dogodi u **zadnjih 30%** snapshota
5. **`rise_start_phase_{suffix}`** — "early" ako je >50% promjene u prvoj polovici snapshota, inače "late"

Izračun ovih feature-a iz raw trajectory podataka (niz odds vrijednosti po vremenu):

```python
def compute_trajectory_features(vals):
    """
    vals: numpy array of odds values sorted by time for one market.
    Returns dict with n_changes, n_direction_changes, max_single_move_pct,
    late_concentration, rise_start_phase.
    """
    result = {}
    if len(vals) < 2:
        return {
            "n_changes": 0, "n_direction_changes": 0,
            "max_single_move_pct": 0, "late_concentration": 0.5,
            "rise_start_phase": "unknown"
        }
    
    changes = np.diff(vals)
    
    # n_changes: number of non-zero changes
    result["n_changes"] = int(np.sum(changes != 0))
    
    # n_direction_changes: count sign flips in non-zero changes
    nonzero = changes[changes != 0]
    if len(nonzero) > 1:
        signs = np.sign(nonzero)
        result["n_direction_changes"] = int(np.sum(signs[1:] != signs[:-1]))
    else:
        result["n_direction_changes"] = 0
    
    # max_single_move_pct: largest absolute change as % of opening odds
    open_val = vals[0]
    if open_val > 0:
        result["max_single_move_pct"] = float(np.max(np.abs(changes)) / open_val * 100)
    else:
        result["max_single_move_pct"] = 0
    
    # late_concentration: fraction of total absolute change in last 30% of snapshots
    total_abs_change = np.sum(np.abs(changes))
    if total_abs_change > 0:
        cutoff = int(len(changes) * 0.7)  # last 30%
        late_change = np.sum(np.abs(changes[cutoff:]))
        result["late_concentration"] = float(late_change / total_abs_change)
    else:
        result["late_concentration"] = 0.5
    
    # rise_start_phase: "early" if >50% of change in first half
    if total_abs_change > 0:
        midpoint = len(changes) // 2
        first_half_change = np.sum(np.abs(changes[:midpoint]))
        result["rise_start_phase"] = "early" if first_half_change > 0.5 * total_abs_change else "late"
    else:
        result["rise_start_phase"] = "unknown"
    
    return result
```

### `change_pct_{suffix}` — ukupni postotak promjene:
```python
change_pct = (close_odds - open_odds) / open_odds * 100
```
Pozitivan znači kvota RASLA (rising), negativan znači PADALA.

---

## CLASSIFY RISING TYPE — Kompletna funkcija

Ova funkcija klasificira TIP kretanja kvote. R7 prihvaća samo DRIFT_UP, CORRECTION_UP i NEGLECT_UP.

```python
def classify_rising_type(row, suffix):
    """
    Klasificira tip rastućeg kretanja kvote.
    
    suffix: "1", "x", ili "2" (za home, draw, away market)
    
    Vraća string: NOT_RISING, LATE_SPIKE_UP, STEAM_UP, OVERREACTION_UP,
                  BOUNCE_UP, NEGLECT_UP, CORRECTION_UP, DRIFT_UP
    """
    cp   = row.get(f"change_pct_{suffix}", 0) or 0       # ukupni % promjene
    nc   = row.get(f"n_changes_{suffix}", 0) or 0         # broj promjena
    ndc  = row.get(f"n_direction_changes_{suffix}", 0) or 0  # promjene smjera
    msm  = row.get(f"max_single_move_{suffix}", 0) or 0   # max single move %
    lc   = row.get(f"late_conc_{suffix}", 0.5)             # late concentration
    ns   = row.get("n_snapshots", 0) or 0                  # ukupni snapshoti

    # Mora biti barem 0.5% rising da uopće klasificiramo
    if cp <= 0.5:
        return "NOT_RISING"

    dc_ratio = ndc / nc if nc > 0 else 0  # omjer promjena smjera

    # LATE_SPIKE_UP — >80% promjene u zadnjih 30% snapshota
    if lc > 0.80 and cp > 3:
        return "LATE_SPIKE_UP"

    # STEAM_UP — jedna velika promjena (>60% ukupne promjene)
    if msm > 0 and cp > 0 and (msm / cp) > 0.60 and cp > 3:
        return "STEAM_UP"

    # OVERREACTION_UP — >15% ukupna promjena
    if cp > 15:
        return "OVERREACTION_UP"

    # BOUNCE_UP — puno promjena smjera (>40%)
    if dc_ratio > 0.40 and cp > 1:
        return "BOUNCE_UP"

    # NEGLECT_UP — kvota raste ali s malo stvarnih promjena
    changes_ratio = nc / ns if ns > 0 else 1
    if changes_ratio < 0.15 and cp > 1:
        return "NEGLECT_UP"

    # CORRECTION_UP — rani rast pa stabilnost
    rsp = row.get(f"rise_start_phase_{suffix}", "unknown")
    if rsp == "early" and lc < 0.3 and dc_ratio < 0.25:
        return "CORRECTION_UP"

    # DRIFT_UP — default smooth rising
    if dc_ratio < 0.25:
        return "DRIFT_UP"

    return "DRIFT_UP"
```

---

## COMPUTE QUALITY SCORE — Kompletna funkcija

Quality score je 0-100 i mjeri KVALITETU rastućeg kretanja. R7 filtira quality >= 70.

```python
def compute_quality_score(row, suffix, league_roi_map, med_speed, med_counter):
    """
    0–100 quality score za RISING outcome.
    
    Parametri:
      row: dict/Series s feature-ima utakmice
      suffix: "1", "x", ili "2"
      league_roi_map: dict {league_name: historical_roi}
      med_speed: float, medijan brzine promjene (cp/n_snapshots) svih rising matches
      med_counter: float, medijan kontra-kretanja suprotnog marketa
    """
    score = 0
    cp   = abs(row.get(f"change_pct_{suffix}", 0) or 0)
    nc   = max(row.get(f"n_changes_{suffix}", 0) or 0, 1)
    ndc  = row.get(f"n_direction_changes_{suffix}", 0) or 0
    ns   = max(row.get("n_snapshots", 1) or 1, 1)
    lc   = row.get(f"late_conc_{suffix}", 0.5)
    orc  = abs(row.get("overround_change", 0) or 0)
    close_odds = row.get(f"close_{suffix}", 3.0) or 3.0

    smoothness = 1 - (ndc / nc)      # 0-1, viši = glađi
    speed      = cp / max(ns, 1)      # % promjene po snapshotu

    # Kontra-kretanje suprotnog marketa
    opposites = {"1": "2", "2": "1", "x": "1"}
    opp = opposites.get(suffix, "2")
    counter = abs(row.get(f"change_pct_{opp}", 0) or 0)

    is_fav = close_odds < 2.0  # favorit?

    # ═══ BONUSI ═══
    # +20  glatki rast (smoothness > 0.7)
    if smoothness > 0.7:     score += 20
    # +15  spori rast (brzina < medijan)
    if speed < med_speed:    score += 15
    # +15  overround stabilan (promjena < 1.0%)
    if orc < 1.0:            score += 15
    # +15  slabo kontra-kretanje (< medijan)
    if counter < med_counter: score += 15
    # +10  promjena nije kasna (late_conc < 50%)
    if lc < 0.5:             score += 10
    # +10  NIJE favorit (odds >= 2.0)
    if not is_fav:           score += 10
    # +10  liga s pozitivnim ROI u povijesti
    lg = row.get("league", "")
    if league_roi_map.get(lg, -1) > 0: score += 10
    # +5   sweet-spot odds (1.80-4.00)
    try:
        co = float(close_odds)
        if 1.80 <= co <= 4.00: score += 5
    except Exception:
        pass

    # ═══ PENALI ═══
    if smoothness < 0.3:     score -= 20   # previše neravnomjerno
    if orc > 3.0:            score -= 20   # overround drastično nestabilan
    if lc > 0.80:            score -= 15   # previše kasna promjena

    return max(0, min(100, score))
```

---

## EXOTIC COUNTRIES (ne-egzotične = sve ostale)

```python
EXOTIC_COUNTRIES = {
    "Aruba", "Barbados", "Bermuda", "Suriname", "Gibraltar", "Andorra",
    "San Marino", "Faroe Islands", "Liechtenstein", "Malta", "Luxembourg",
    "Nicaragua", "El Salvador", "Honduras", "Guatemala", "Panama",
    "Dominican Republic", "Trinidad and Tobago", "Jamaica", "Guam",
    "Tahiti", "New Caledonia", "Fiji", "Samoa", "Tonga", "Vanuatu",
}
```

`non_exotic` = svaka liga čija `country` NIJE u EXOTIC_COUNTRIES.

---

## MEDIJAN REFERENTNE VRIJEDNOSTI (med_speed, med_counter)

Ove vrijednosti se računaju iz **završenih utakmica** u datasetu:

```python
# Izračunaj med_speed (medijan brzine promjene za sve rising outcomes)
speeds = []
for suffix in ("1", "x", "2"):
    rising_mask = mdf[f"change_pct_{suffix}"] > 0.5  # rising
    rising = mdf[rising_mask]
    ns_vals = rising["n_snapshots"].replace(0, 1).fillna(1)
    cp_vals = rising[f"change_pct_{suffix}"].fillna(0).abs()
    speeds.extend((cp_vals / ns_vals).tolist())
med_speed = np.median(speeds) if speeds else 0.5

# Izračunaj med_counter (medijan kontra-kretanja za sve rising outcomes)
counter_vals = []
opp_map = {"1": "2", "x": "1", "2": "1"}
for suffix in ("1", "x", "2"):
    rising_mask = mdf[f"change_pct_{suffix}"] > 0.5
    rising = mdf[rising_mask]
    opp = opp_map[suffix]
    counter_vals.extend(rising[f"change_pct_{opp}"].fillna(0).abs().tolist())
med_counter = np.median(counter_vals) if counter_vals else 3.0
```

**Fallback vrijednosti** (ako nema dovoljno podataka): `med_speed = 0.5`, `med_counter = 3.0`

---

## LEAGUE ROI MAP

Historical ROI po ligi. U mojem backtest sustavu, ovo se računa iz rezultata svih rising utakmica po ligi. Za početak koristiš prazan dict `{}` — tada quality score gubi max 10 bodova za tu komponentu, ali ostali bonusi i dalje rade.

Ako želiš implementirati:
```python
# Iz završenih utakmica s rising odds
# Za svaku ligu, izračunaj: (ukupni profit od flat bet @ closing odds) / (ukupni ulog)
league_roi_map = {}
for league, group in finished_rising.groupby("league"):
    # Ako je odds_side bio predikcija (rising side):
    # profit = sum(odds - 1 za win, -1 za loss) / n
    won = group[group["hit"] == True]  # match pogođen
    lost = group[group["hit"] == False]
    total_profit = won["close_odds"].sum() - len(won) - len(lost)  # net
    roi = total_profit / len(group) * 100 if len(group) > 0 else -100
    league_roi_map[league] = roi
```

Za početak, možeš koristiti `league_roi_map = {}` — R7 i dalje radi, samo taj bonus neće biti aktivan.

---

## GHOST MATCH FILTERING

Problem: ista utakmica može biti "upcoming" u jednom ciklusu i "finished" u drugom (jer match_id ne sadrži datum). Rješenje:

```python
# Skupi sve team-pair-ove koji su ikad bili "finished"
finished_pairs = set()
for _, row in all_data.iterrows():
    if str(row.get("status", "")).strip().lower() == "finished":
        pair = f"{row['home']}|{row['away']}"
        finished_pairs.add(pair)

# Filtriraj upcoming — izbaci sve parove koji su ikad bili finished
upcoming = upcoming[~upcoming.apply(
    lambda r: f"{r['home']}|{r['away']}" in finished_pairs, axis=1
)]
```

---

## ŠTO TOČNO TREBAŠ NAPRAVITI

### 1. Dodaj nove feature-e u `compute_features()`

U postojeću `compute_features()` funkciju dodaj `traj_features` za izračun:
- `n_changes_{side}` (za 1, x, 2)
- `n_direction_changes_{side}`
- `max_single_move_{side}` (kao % od open odds)
- `late_conc_{side}` (late concentration)
- `rise_start_phase_{side}` ("early" ili "late")
- `change_pct_{side}` ← već postoji kao `pct_change_{side}`, preimenuj ili dodaj alias

### 2. Zamijeni `classify_signal()` s `classify_rising_type()`

Koristi kompletnu funkciju iz specifikacije gore. Razlika: nova klasifikacija koristi trajectory feature-e (n_changes, n_direction_changes, max_single_move, late_conc, rise_start_phase), dok stara koristi samo pct_change i mono.

### 3. Zamijeni `calculate_composite_score()` s `compute_quality_score()`

Nova quality score funkcija (0-100) umjesto starog composite score-a (0-80+).

### 4. Primijeni R7 filter umjesto score≥50

U `run_scan()`:
```python
# STARO:
if s["score"] >= threshold:
    # record signal

# NOVO:
# Za SVAKI rising market u upcoming utakmici:
#   1. classify_rising_type() → rtype
#   2. Provjeri: rtype in ("DRIFT_UP", "CORRECTION_UP", "NEGLECT_UP")
#   3. compute_quality_score() → quality  
#   4. Provjeri: quality >= 70
#   5. Provjeri: 1.80 <= close_odds <= 4.00
#   6. Provjeri: country NOT IN EXOTIC_COUNTRIES
#   SVI uvjeti moraju proći → signal!
```

### 5. Ghost match filtering

Dodaj ghost match filtering prije generiranja signala (objašnjeno gore).

### 6. Medijan referentne vrijednosti

Izračunaj `med_speed` i `med_counter` iz finished podataka pri svakom scanu (ili koristi fallback 0.5 i 3.0 ako nema dovoljno podataka).

### 7. League ROI map

Za početak koristi prazan dict: `league_roi_map = {}`. Kasnije se može dodati.

### 8. Ažuriraj paper trade recording

Umjesto dual account (A: ≥50, B: ≥55), koristi jedan account s R7 filterom. Flat stake 2% bankrolla po okladi.

### 9. Ažuriraj win probability estimate

Za R7 signale: `est_winprob = 0.449` (44.9% iz backtesta).

### 10. Ažuriraj display i reporting

Prikaži: quality score, rising_type, close odds, league, change_pct u signal outputu.

---

## PRIMJER OČEKIVANOG OUTPUTA NAKON IMPLEMENTACIJE

```
═══════════════════════════════════════════════════════════════
  R7 SCANNER — 2026-02-24 10:30:00
═══════════════════════════════════════════════════════════════
  Loaded 5,432 rows from 45 files
  Upcoming: 128 matches (ghost-filtered: 94)
  med_speed=0.431, med_counter=2.876

  R7 SIGNALS (quality≥70, odds 1.80-4.00, non-exotic, rising type OK):
──────────────────────────────────────────────────────────────

  [Q90] HSV vs RB Leipzig | HOME @ 3.50 | DRIFT_UP | 2. Bundesliga
        Quality: smooth(+20) slow(+15) orc_ok(+15) weak_ctr(+15) 
                 not_late(+10) not_fav(+10) sweet(+5) = 90
        Change: +4.2% | Snapshots: 42

  [Q70] Roma vs Juventus | AWAY @ 2.85 | CORRECTION_UP | Serie A
        Quality: smooth(+20) orc_ok(+15) not_late(+10) not_fav(+10) 
                 league_roi(+10) sweet(+5) = 70
        Change: +3.1% | Snapshots: 38

──────────────────────────────────────────────────────────────
  Found 3 R7 signals
```

---

## VAŽNE NAPOMENE

1. **R7 filtrira SAMO RISING outcomes** (change_pct > 0.5%). NE tražimo padajuće kvote.
2. **Bet side**: Kada kvota na "1" (home) RASTE, to znači da market smatra home MANJE vjerojatnijim. Ali R7 betta upravo NA tu stranu (kontrarian). Dakle, ako kvota za home raste → bet HOME.
3. **Market X (remi)**: R7 uključuje i remi. Provjeravaj sva tri marketa (1, X, 2).
4. **Dedup**: Jedna oklada po utakmici — zadrži onu s najvišim quality score-om.
5. **Staking**: Flat 2% bankrolla (€20 za bankroll €1000).
6. **R7 NIJE ML model** — čisti rule-based filter. Nema treniranja, nema modela. Samo pravila.

---

## SAŽETAK PROMJENA U trader.py

| Što | Staro | Novo |
|-----|-------|------|
| Signal klasifikacija | `classify_signal()` (5 tipova, koristi samo pct+mono) | `classify_rising_type()` (7 tipova, koristi trajectory features) |
| Scoring | `calculate_composite_score()` (0-80+) | `compute_quality_score()` (0-100) |
| Filter | `score >= 50` | `quality >= 70 AND odds 1.80-4.00 AND non_exotic AND good_type` |
| Feature-i | `pct_change, mono, vol, smooth_rise` | + `n_changes, n_direction_changes, max_single_move, late_conc, rise_start_phase` |
| Accounts | Dual (A: ≥50, B: ≥55) | Single R7 account |
| Win prob | 0.458 base | 0.449 (R7 backtest) |
| Ghost filter | Nema | Team-pair exclusion za finished parove |
