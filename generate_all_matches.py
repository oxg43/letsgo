#!/usr/bin/env python3
"""
Generate ALL_MATCHES TSV with odds movements for all tracked matches.
Includes R7 filter column showing which matches pass R7 quality filter.
Run manually whenever you need fresh data, or called automatically by runner.

Usage: python generate_all_matches.py
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from datetime import datetime

def generate_all_matches_tsv(verbose=True, **kwargs):
    """Generate ALL_MATCHES TSV. Returns (path, count, steam_count)."""
    csv_dir = Path('odds_data/movement')
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Find today's CSV files + yesterday's for opening odds context
    today_files = sorted(csv_dir.glob(f'{today}*.csv'))
    if not today_files:
        # Fallback to most recent date
        all_files = sorted(csv_dir.glob('*.csv'))
        if all_files:
            latest_date = all_files[-1].name[:10]
            today_files = sorted(csv_dir.glob(f'{latest_date}*.csv'))
            today = latest_date
    
    # Also include yesterday's files for better opening odds
    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    yesterday_files = sorted(csv_dir.glob(f'{yesterday}*.csv'))
    all_scan_files = yesterday_files + today_files
    
    if verbose:
        print(f'📊 Generiranje ALL_MATCHES za {today}')
        print(f'   Učitavam {len(all_scan_files)} CSV fajlova ({len(yesterday_files)} jučer + {len(today_files)} danas)...')
    
    if len(all_scan_files) < 1:
        if verbose:
            print('   Nema fajlova')
        return None, 0, 0
    
    # ── Pass 1: scan ALL files to collect opening odds, latest odds, 
    #    snapshot counts, and metadata for EVERY match seen today ──────────
    first_seen = {}    # (home,away) → {odds_1, odds_x, odds_2} from first appearance
    last_seen = {}     # (home,away) → full row dict from last appearance while upcoming
    snap_counts = defaultdict(int)  # (home,away) → count of files where seen as upcoming
    match_meta = {}    # (home,away) → {match_date, kick_off, country, league}
    # R7: collect ALL odds snapshots per match for trajectory features
    odds_history = defaultdict(list)  # (home,away) → list of (odds_1, odds_x, odds_2)
    
    for i, fpath in enumerate(all_scan_files):
        try:
            df = pd.read_csv(fpath, sep=';', dtype=str)
        except Exception:
            continue
        for _, r in df.iterrows():
            key = (r.get('home', ''), r.get('away', ''))
            if not key[0]:
                continue
            
            is_upcoming = r.get('status') == 'upcoming'
            
            # Count snapshots while match was upcoming
            if is_upcoming:
                snap_counts[key] += 1
            
            # Collect odds history for R7 trajectory features
            try:
                h1 = float(r.get('odds_1', 0) or 0)
                hx = float(r.get('odds_x', 0) or 0)
                h2 = float(r.get('odds_2', 0) or 0)
                if h1 > 1 and hx > 1 and h2 > 1:
                    odds_history[key].append((h1, hx, h2))
            except (ValueError, TypeError):
                pass
            
            # Save opening odds (first time we see this match)
            if key not in first_seen:
                try:
                    first_seen[key] = {
                        'odds_1': float(r.get('odds_1', 0) or 0),
                        'odds_x': float(r.get('odds_x', 0) or 0),
                        'odds_2': float(r.get('odds_2', 0) or 0),
                    }
                except (ValueError, TypeError):
                    pass
                # Save metadata from first appearance
                match_meta[key] = {
                    'match_date': r.get('match_date', today),
                    'kick_off': r.get('kick_off', '00:00'),
                    'country': r.get('country', ''),
                    'league': r.get('league', ''),
                }
            
            # Save latest odds (last time we see this match while upcoming)
            if is_upcoming:
                try:
                    last_seen[key] = {
                        'odds_1': float(r.get('odds_1', 0) or 0),
                        'odds_x': float(r.get('odds_x', 0) or 0),
                        'odds_2': float(r.get('odds_2', 0) or 0),
                        'status': 'upcoming',
                    }
                except (ValueError, TypeError):
                    pass
            # Mark finished matches
            if not is_upcoming and key not in last_seen:
                last_seen[key] = {'status': 'finished'}
            if not is_upcoming and key in last_seen:
                last_seen[key]['status'] = r.get('status', 'finished')
                
        if verbose and (i + 1) % 50 == 0:
            print(f'   ... {i+1}/{len(all_scan_files)} fajlova obrađeno')
    
    if verbose:
        print(f'   ... svih {len(all_scan_files)} fajlova obrađeno, {len(first_seen)} mečeva pronađeno')
    
    # ── Pass 2: build rows only for UPCOMING matches ───────────────────
    # Import R7 functions from trader
    try:
        from trader import (compute_trajectory_features, classify_rising_type,
                            compute_quality_score, EXOTIC_COUNTRIES, R7_ACCEPTED_TYPES)
        r7_available = True
    except ImportError:
        r7_available = False
    
    # Compute med_speed and med_counter from finished matches for R7
    med_speed = 0.5
    med_counter = 3.0
    if r7_available:
        finished_speeds = []
        finished_counters = []
        for key in first_seen:
            closing = last_seen.get(key, {})
            if closing.get('status', 'upcoming') == 'upcoming':
                continue
            opening = first_seen[key]
            o1, ox, o2 = opening.get('odds_1', 0), opening.get('odds_x', 0), opening.get('odds_2', 0)
            l1 = closing.get('odds_1', o1)
            lx = closing.get('odds_x', ox)
            l2 = closing.get('odds_2', o2)
            ns = max(snap_counts.get(key, 1), 1)
            for ov, lv, opp_o, opp_l in [(o1, l1, o2, l2), (ox, lx, o1, l1), (o2, l2, o1, l1)]:
                if ov > 0:
                    cp = (lv - ov) / ov * 100
                    if cp > 0.5:
                        finished_speeds.append(abs(cp) / ns)
                        if opp_o > 0:
                            finished_counters.append(abs((opp_l - opp_o) / opp_o * 100))
        if finished_speeds:
            med_speed = float(np.median(finished_speeds))
        if finished_counters:
            med_counter = float(np.median(finished_counters))
    
    rows = []
    r7_count = 0
    for key in first_seen:
        # Skip finished matches — only include upcoming
        closing = last_seen.get(key, {})
        if closing.get('status', 'upcoming') != 'upcoming':
            continue
        
        opening = first_seen[key]
        meta = match_meta.get(key, {})
        
        o1 = opening.get('odds_1', 0)
        ox = opening.get('odds_x', 0)
        o2 = opening.get('odds_2', 0)
        
        # Use closing odds if available, otherwise opening = closing
        l1 = closing.get('odds_1', o1)
        lx = closing.get('odds_x', ox)
        l2 = closing.get('odds_2', o2)
        
        pct1 = (l1 - o1) / o1 * 100 if o1 > 0 else 0
        pctx = (lx - ox) / ox * 100 if ox > 0 else 0
        pct2 = (l2 - o2) / o2 * 100 if o2 > 0 else 0
        
        steam = []
        if abs(pct1) >= 10:
            steam.append('1')
        if abs(pctx) >= 10:
            steam.append('X')
        if abs(pct2) >= 10:
            steam.append('2')
        
        # ── R7 filter evaluation ──
        r7_label = ''
        r7_quality = ''
        if r7_available:
            country = meta.get('country', '')
            if country not in EXOTIC_COUNTRIES:
                # Build trajectory features from odds_history
                history = odds_history.get(key, [])
                ns = snap_counts.get(key, 1)
                
                # Compute overround change
                or_open = 0
                or_close = 0
                if o1 > 0 and ox > 0 and o2 > 0:
                    or_open = (1/o1 + 1/ox + 1/o2)
                if l1 > 0 and lx > 0 and l2 > 0:
                    or_close = (1/l1 + 1/lx + 1/l2)
                orc = (or_close - or_open) * 100  # percentage points
                
                best_r7 = None
                best_q = 0
                
                for suffix, ov, lv, pct_val in [('1', o1, l1, pct1), ('x', ox, lx, pctx), ('2', o2, l2, pct2)]:
                    if pct_val <= 0.5 or lv <= 1.01:
                        continue
                    if not (1.80 <= lv <= 4.00):
                        continue
                    
                    # Compute trajectory features for this market
                    if len(history) >= 2:
                        col_idx = {'1': 0, 'x': 1, '2': 2}[suffix]
                        vals = np.array([h[col_idx] for h in history])
                        tf = compute_trajectory_features(vals)
                    else:
                        tf = {'n_changes': 0, 'n_direction_changes': 0,
                              'max_single_move_pct': 0, 'late_conc': 0.5,
                              'rise_start_phase': 'unknown'}
                    
                    # Build row dict for R7 functions
                    opp_map = {'1': '2', '2': '1', 'x': '1'}
                    opp = opp_map[suffix]
                    opp_pct = {'1': pct1, 'x': pctx, '2': pct2}[opp]
                    
                    row_dict = {
                        f'change_pct_{suffix}': pct_val,
                        f'n_changes_{suffix}': tf['n_changes'],
                        f'n_direction_changes_{suffix}': tf['n_direction_changes'],
                        f'max_single_move_{suffix}': tf['max_single_move_pct'],
                        f'late_conc_{suffix}': tf['late_conc'],
                        f'rise_start_phase_{suffix}': tf['rise_start_phase'],
                        f'close_{suffix}': lv,
                        f'change_pct_{opp}': opp_pct,
                        'n_snapshots': ns,
                        'overround_change': orc,
                        'league': meta.get('league', ''),
                    }
                    
                    rtype = classify_rising_type(row_dict, suffix)
                    if rtype not in R7_ACCEPTED_TYPES:
                        continue
                    
                    quality = compute_quality_score(row_dict, suffix, {}, med_speed, med_counter)
                    if quality >= 70 and quality > best_q:
                        best_q = quality
                        side_name = {'1': 'HOME', 'x': 'DRAW', '2': 'AWAY'}[suffix]
                        best_r7 = f'{side_name}@{lv:.2f}'
                
                if best_r7:
                    r7_label = best_r7
                    r7_quality = str(best_q)
                    r7_count += 1
        
        rows.append({
            'date': meta.get('match_date', today),
            'kick_off': meta.get('kick_off', '00:00'),
            'country': meta.get('country', ''),
            'league': meta.get('league', ''),
            'home': key[0],
            'away': key[1],
            'open_1': round(o1, 2) if o1 else '',
            'latest_1': round(l1, 2) if l1 else '',
            'change_1': f'{pct1:+.1f}%',
            'open_X': round(ox, 2) if ox else '',
            'latest_X': round(lx, 2) if lx else '',
            'change_X': f'{pctx:+.1f}%',
            'open_2': round(o2, 2) if o2 else '',
            'latest_2': round(l2, 2) if l2 else '',
            'change_2': f'{pct2:+.1f}%',
            'snapshots': snap_counts.get(key, 1),
            'steam_on': ','.join(steam) if steam else '',
            'R7': r7_label,
            'R7_Q': r7_quality,
        })
    
    # Create DataFrame and sort
    df_out = pd.DataFrame(rows)
    
    # Optional: filter by minimum kick_off time (set via env var or kwarg)
    min_kickoff = kwargs.get('min_kickoff', None)
    if min_kickoff and 'kick_off' in df_out.columns:
        before = len(df_out)
        df_out = df_out[df_out['kick_off'] >= min_kickoff].copy()
        if verbose:
            print(f'   Filter kick_off >= {min_kickoff}: {before} → {len(df_out)} utakmica')
    
    df_out = df_out.sort_values(['date', 'kick_off'])
    
    # Save
    out_path = Path(f'odds_data/signal_map/ALL_MATCHES_{today}.tsv')
    df_out.to_csv(out_path, sep='\t', index=False)
    
    steam_count = len([r for r in rows if r['steam_on']])
    
    # Auto-save R7 signals TSV + detect new signals
    r7_path = None
    new_r7_signals = []
    if 'R7' in df_out.columns:
        r7_df = df_out[df_out['R7'].notna() & (df_out['R7'] != '')].copy()
        r7_df = r7_df.sort_values(['date', 'kick_off'])
        r7_path = Path(f'odds_data/signal_map/R7_SIGNALS_{today}.tsv')

        # Compare with previous R7 TSV to detect new signals
        old_keys = set()
        if r7_path.exists():
            try:
                old_r7 = pd.read_csv(r7_path, sep='\t', dtype=str)
                for _, row in old_r7.iterrows():
                    old_keys.add((row.get('home', ''), row.get('away', ''), row.get('date', '')))
            except Exception:
                pass

        for _, row in r7_df.iterrows():
            key = (row.get('home', ''), row.get('away', ''), row.get('date', ''))
            if key not in old_keys:
                new_r7_signals.append(row)

        # Save the new TSV
        r7_df.to_csv(r7_path, sep='\t', index=False)

        # Always print new R7 signals (even when verbose=False)
        if new_r7_signals:
            print(f'\n  🆕 NOVI R7 SIGNALI ({len(new_r7_signals)}):')
            print(f'  {"─"*70}')
            for sig in new_r7_signals:
                q = sig.get('R7_Q', '')
                r7 = sig.get('R7', '')
                dt = sig.get('date', '')
                ko = sig.get('kick_off', '')
                h = sig.get('home', '')
                a = sig.get('away', '')
                lg = sig.get('league', '')
                print(f'  ⚡ {dt} {ko}  {h} vs {a}  │  {r7}  Q{q}  │  {lg}')
            print(f'  {"─"*70}\n')

        if verbose:
            print(f'✅ R7 signali: {r7_path} ({len(r7_df)} mečeva)')
    
    if verbose:
        print(f'')
        print(f'✅ Spremljeno: {out_path}')
        print(f'   Ukupno: {len(df_out)} upcoming utakmica')
        print(f'   Sa STEAM (>=10%): {steam_count}')
        print(f'   R7 signali: {r7_count}')
        print(f'   Sortirano po: date → kick_off')
    
    return out_path, len(df_out), steam_count

def main():
    import sys
    kwargs = {}
    for arg in sys.argv[1:]:
        if arg.startswith('--from='):
            kwargs['min_kickoff'] = arg.split('=', 1)[1]
    generate_all_matches_tsv(verbose=True, **kwargs)

if __name__ == '__main__':
    main()
