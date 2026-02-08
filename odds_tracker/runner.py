"""
Main Runner — Odds Tracker System
Continuously scrapes OddsPortal, analyzes market movements,
and generates betting signals.

Usage:
    python -m odds_tracker.runner              # Run continuously (default: every 2 min)
    python -m odds_tracker.runner --once       # Run one cycle
    python -m odds_tracker.runner --report     # Generate end-of-day report
    python -m odds_tracker.runner --signals    # Show current signals
"""
import argparse
import csv
import io
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Fix Windows console encoding for Unicode symbols
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

from odds_tracker.config import (
    ODDSPORTAL_LIVE_URL,
    SCRAPE_INTERVAL_SECONDS, HEADLESS,
    DATA_DIR, REPORTS_DIR, PAGE_TIMEOUT_MS,
    get_oddsportal_url, get_tomorrow_url, get_clean_csv,
)
from odds_tracker.database import (
    init_db, save_snapshot, save_live_snapshot, get_snapshot_count,
    get_unique_match_count, get_live_snapshot_count,
    get_all_upcoming_matches, get_all_signals_today,
    get_prematch_odds_bulk,
)
from odds_tracker.scraper import scrape_odds
from odds_tracker.live_scraper import scrape_live_odds
from odds_tracker.analyzer import analyze_match, get_movement_summary
from odds_tracker.signals import generate_signals, format_signal, save_signals_to_db
from odds_tracker.movement_logger import save_cycle_snapshot, generate_movement_summary
from odds_tracker.signal_map import (
    write_live_signals, capture_final_bets, capture_zlatna_pravila,
    format_golden_signal, format_zlatna_signal, print_signal_map,
)
from odds_tracker.match_watcher import check_and_alert
from odds_tracker.value_betting import (
    analyze_prematch_value, analyze_all_live, format_value_bet, format_value_bet_short,
)
from odds_tracker.research import analyze_upcoming_match


def run_one_cycle(cycle_num: int = 1, watch_list: list[str] | None = None, watch_edge: float | None = None, watch_delta: float | None = None, watch_conf: float | None = None) -> tuple[int, list[dict]]:
    """
    Run one scrape-analyze-signal cycle.
    Returns (num_matches_scraped, signals).
    """
    print(f"\n{'='*70}")
    print(f"  CYCLE #{cycle_num} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    # ── 1. SCRAPE ──
    # Refresh URLs each cycle (auto-rolls at midnight)
    today_url = get_oddsportal_url()
    tomorrow_url = get_tomorrow_url()
    print(f"\n[1/4] Scraping OddsPortal (today: {today_url.split('/')[-2]})...")
    matches = scrape_odds(today_url, headless=HEADLESS, timeout_ms=PAGE_TIMEOUT_MS)

    if not matches:
        print("  ⚠ No matches scraped. Will retry next cycle.")
        return 0, []

    # Also scrape tomorrow (every 5th cycle or first cycle)
    if cycle_num == 1 or cycle_num % 5 == 0:
        print(f"\n  Scraping tomorrow's matches ({tomorrow_url.split('/')[-2]})...")
        try:
            tomorrow_matches = scrape_odds(tomorrow_url, headless=HEADLESS, timeout_ms=PAGE_TIMEOUT_MS)
            if tomorrow_matches:
                # Mark them as tomorrow's
                for m in tomorrow_matches:
                    m['_day'] = 'tomorrow'
                matches.extend(tomorrow_matches)
                print(f"  [OK] +{len(tomorrow_matches)} tomorrow matches added")
        except Exception as e:
            print(f"  ⚠ Tomorrow scrape failed: {e}")

    if not matches:
        print("  ⚠ No matches scraped. Will retry next cycle.")
        return 0, []

    # Filter to upcoming only for saving (we still save finished for reference)
    upcoming = [m for m in matches if m.get('status') == 'upcoming']
    finished = [m for m in matches if m.get('status') == 'finished']

    print(f"  [OK] Scraped {len(matches)} matches ({len(upcoming)} upcoming, {len(finished)} finished)")

    # Save all to database
    saved = save_snapshot(matches)
    total_snapshots = get_snapshot_count()
    unique_matches = get_unique_match_count()
    print(f"  [OK] Saved to DB. Total: {total_snapshots} snapshots across {unique_matches} unique matches")

    # Save CSV snapshot for later analysis
    csv_path = save_cycle_snapshot(matches, cycle_num)
    print(f"  [OK] Odds movement saved to {csv_path.name}")

    # ── 2. ANALYZE ──
    print("\n[2/3] Analyzing market movements...")

    # Identify matches approaching kick-off (within 2 hours)
    now = datetime.now()
    priority_matches = []
    regular_matches = []

    for m in upcoming:
        ko = m.get('kick_off', '')
        if ko:
            try:
                today_str = now.strftime('%Y-%m-%d')
                ko_dt = datetime.strptime(f"{today_str} {ko}", '%Y-%m-%d %H:%M')
                minutes_to_ko = (ko_dt - now).total_seconds() / 60
                m['_minutes_to_ko'] = minutes_to_ko
                if 0 < minutes_to_ko <= 120:
                    priority_matches.append(m)
                elif minutes_to_ko > 0:
                    regular_matches.append(m)
            except ValueError:
                regular_matches.append(m)

    # Sort priority by time to kick-off
    priority_matches.sort(key=lambda m: m.get('_minutes_to_ko', 9999))

    if priority_matches:
        print(f"\n  🎯 {len(priority_matches)} matches within 2 hours of kick-off:")
        for m in priority_matches[:15]:
            mins = m.get('_minutes_to_ko', 0)
            analysis = analyze_match(m['home'], m['away'], m.get('kick_off', ''))
            if analysis['snapshots'] >= 2:
                summary = get_movement_summary(analysis)
                print(f"\n  [{mins:.0f} min to KO]")
                for line in summary.split('\n'):
                    print(f"    {line}")

    # ── 3. SIGNALS ──
    print("\n[3/3] Generating signals...")
    signals = generate_signals(upcoming)

    if signals:
        save_signals_to_db(signals)
        print(f"\n  🎰 {len(signals)} SIGNALS Generated:")
        print(f"  {'─'*60}")
        for s in signals[:20]:
            formatted = format_signal(s)
            for line in formatted.split('\n'):
                print(f"    {line}")
            print()
    else:
        print("  ℹ No strong signals yet. Need more data points (keep scraping).")
        if total_snapshots < 3:
            print("  💡 Signals require at least 3 snapshots per match. Keep running!")

    # ── 4. SIGNAL MAP + VALUE ANALYSIS (always runs, even without steam signals) ──
    value_bets = []

    # 4a. Pre-match value from steam signals (if any)
    if signals:
        for s in signals:
            pm_odds = {
                '1': s.get('opening_odds', 0),
                'X': s.get('opening_odds', 0),
                '2': s.get('opening_odds', 0),
            }
            cur_odds = {
                '1': s.get('latest_odds', 0) if s.get('bet') == '1' else 0,
                'X': s.get('latest_odds', 0) if s.get('bet') == 'X' else 0,
                '2': s.get('latest_odds', 0) if s.get('bet') == '2' else 0,
            }
            try:
                analysis = analyze_match(s['home'], s['away'], s.get('kick_off', ''))
                if analysis['opening_odds']['1'] and analysis['latest_odds']['1']:
                    pm_odds = {k: v for k, v in analysis['opening_odds'].items() if v}
                    cur_odds = {k: v for k, v in analysis['latest_odds'].items() if v}
                    vb = analyze_prematch_value(s, pm_odds, cur_odds)
                    if vb:
                        value_bets.append(vb)
            except Exception:
                pass

    # 4b. Deep value analysis for ALL upcoming matches (regardless of signals)
    deep_value_count = 0
    for m in upcoming[:100]:
        try:
            analysis = analyze_match(m['home'], m['away'], m.get('kick_off', ''))
            if analysis['snapshots'] < 2:
                continue
            opening = analysis.get('opening_odds', {})
            latest = analysis.get('latest_odds', {})
            if not opening.get('1') or not latest.get('1'):
                continue
            deep_values = analyze_upcoming_match(
                m, opening, latest, num_snapshots=analysis['snapshots']
            )
            for dv in deep_values:
                # Avoid duplicates with signal-based value bets
                key = (dv.get('home', ''), dv.get('away', ''), dv.get('bet', ''))
                if not any(
                    (vb.get('home', ''), vb.get('away', ''), vb.get('bet', '')) == key
                    for vb in value_bets
                ):
                    value_bets.append(dv)
                    deep_value_count += 1
        except Exception:
            pass

    if deep_value_count:
        print(f"\n  🔍 {deep_value_count} deep pre-match value bets found (all upcoming)")

    # 4c. ALWAYS write LIVE_SIGNALS and check FINAL_BETS
    all_signal_entries = signals.copy() if signals else []

    # Add research-based value bets as signal entries for the TSV
    for dv in value_bets:
        if dv.get('type') == 'PRE_MATCH_VALUE' and not any(
            s.get('home') == dv.get('home') and s.get('away') == dv.get('away')
            and s.get('bet') == dv.get('bet')
            for s in all_signal_entries
        ):
            all_signal_entries.append({
                'home': dv.get('home', ''),
                'away': dv.get('away', ''),
                'kick_off': dv.get('kick_off', ''),
                'bet': dv.get('bet', ''),
                'signal_type': 'DEEP_VALUE',
                'latest_odds': dv.get('offered_odds', 0),
                'opening_odds': dv.get('offered_odds', 0),
                'confidence': dv.get('confidence', 0.5),
                'pct_change': dv.get('pct_change', 0),
                'reason': dv.get('reason', ''),
            })

    live_count = write_live_signals(all_signal_entries, value_bets)
    print(f"\n  [LIVE] {live_count} signals written to LIVE_SIGNALS TSV")

    if value_bets:
        print(f"\n  💰 {len(value_bets)} VALUE BETS detected (pre-match):")
        print(f"  {'─'*70}")
        for vb in value_bets[:10]:
            print(format_value_bet(vb))
            print()

    # Capture golden window (5-9 min before KO) to FINAL_BETS
    final_new = capture_final_bets(all_signal_entries, value_bets)
    if final_new:
        print(f"  \U0001f534 FINAL BETS — {len(final_new)} NEW signals captured (5-9 min before KO):")
        print(f"  {'\u2500'*70}")
        for row in final_new:
            print(format_golden_signal(row))
        print(f"  {'\u2500'*70}")

    # Capture ZLATNA PRAVILA — only the BEST signals (golden rules filter)
    zlatna_new = capture_zlatna_pravila(all_signal_entries, value_bets)
    if zlatna_new:
        print(f"\n  \u2B50 ZLATNA PRAVILA — {len(zlatna_new)} BEST bets captured (golden rules):")
        print(f"  {'\u2500'*70}")
        for row in zlatna_new:
            print(format_zlatna_signal(row))
        print(f"  {'\u2500'*70}")
    else:
        # Always show status
        print(f"  [ZLATNA] No new golden-rule bets this cycle (filters: STEAM/LATE_SHARP, >=80% conf, >=8% drop, >=30 snaps)")

    # ── 5. LIVE IN-PLAY SCRAPING + VALUE BETTING ──
    print(f"\n[LIVE] Scraping live in-play matches...")
    try:
        live_matches = scrape_live_odds(headless=HEADLESS, timeout_ms=PAGE_TIMEOUT_MS)
        if live_matches:
            saved_live = save_live_snapshot(live_matches)
            live_total = get_live_snapshot_count()
            print(f"  [OK] {len(live_matches)} live matches scraped (DB: {live_total} live snapshots)")

            # Get pre-match odds for comparison
            prematch_data = get_prematch_odds_bulk()
            live_values = analyze_all_live(live_matches, prematch_data)

            if live_values:
                print(f"\n  💰 {len(live_values)} LIVE VALUE BETS detected:")
                print(f"  {'─'*70}")
                for lv in live_values[:15]:
                    print(format_value_bet(lv))
                    print()

                # Write live value bets to a separate TSV
                _save_live_value_tsv(live_values)

                # Also capture live value bets to ZLATNA_PRAVILA
                zlatna_live = _capture_zlatna_live(live_values)
                if zlatna_live:
                    print(f"  ⭐ ZLATNA PRAVILA — {len(zlatna_live)} LIVE bets captured!")
                    for row in zlatna_live:
                        print(format_zlatna_signal(row))
                # --- Run match watcher for any watched matches (console alerts) ---
                if watch_list:
                    try:
                        alerts = check_and_alert(
                            live_values,
                            watch_list,
                            edge_min=(watch_edge if watch_edge is not None else 0.12),
                            delta_edge=(watch_delta if watch_delta is not None else 0.05),
                            conf_min=(watch_conf if watch_conf is not None else 0.65),
                        )
                        if alerts:
                            print(f"\n  🔔 {len(alerts)} WATCH ALERTS generated")
                    except Exception as e:
                        print(f"  ⚠ Watcher error: {e}")
            else:
                print("  ℹ No live value bets found this cycle.")
        else:
            print("  ℹ No live matches currently playing.")
    except Exception as e:
        import traceback
        print(f"  ⚠ Live scraping error: {e}")
        traceback.print_exc()

    # Log cycle completion
    _log_cycle(cycle_num, len(matches), len(signals))

    return len(matches), signals


def _save_live_value_tsv(value_bets: list[dict]):
    """Save live value bets to a TSV file (overwritten each cycle)."""
    import csv
    tsv_dir = DATA_DIR / "signal_map"
    tsv_dir.mkdir(exist_ok=True)
    tsv_path = tsv_dir / f"LIVE_VALUE_{datetime.now().strftime('%Y-%m-%d')}.tsv"

    columns = ['minute', 'score', 'match', 'bet', 'odds', 'fair_odds',
               'edge', 'model_prob', 'kelly', 'confidence', 'tier', 'country', 'league', 'reason']

    with open(tsv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter='\t')
        writer.writeheader()
        for vb in value_bets:
            writer.writerow({
                'minute': vb.get('minute', ''),
                'score': vb.get('score', ''),
                'match': f"{vb['home']} vs {vb['away']}",
                'bet': vb.get('bet_label', vb.get('bet', '')),
                'odds': f"{vb.get('offered_odds', 0):.2f}",
                'fair_odds': f"{vb.get('fair_odds', 0):.2f}",
                'edge': f"{vb.get('edge', 0):+.1%}",
                'model_prob': f"{vb.get('model_prob', 0):.0%}",
                'kelly': f"{vb.get('kelly_stake', 0):.1%}",
                'confidence': f"{vb.get('confidence', 0):.0%}",
                'tier': f"T{vb.get('tier', '?')}",
                'country': vb.get('country', ''),
                'league': vb.get('league', ''),
                'reason': vb.get('reason', ''),
            })


def _capture_zlatna_live(live_values: list[dict]) -> list[dict]:
    """
    Capture best live value bets to ZLATNA_PRAVILA file.
    Uses strict golden rules: edge >=12%, confidence >=65%, tier 3-4,
    minute 15-70, home leading or away leading (not recovery).
    """
    import csv
    import math
    from odds_tracker.signal_map import SIGNAL_MAP_DIR, ZLATNA_COLUMNS

    now = datetime.now()
    tsv_path = SIGNAL_MAP_DIR / f"ZLATNA_PRAVILA_{now.strftime('%Y-%m-%d')}.tsv"

    # Load existing keys to prevent duplicates
    existing_keys = set()
    if tsv_path.exists():
        try:
            with open(tsv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    key = f"{row.get('match', '')}|{row.get('bet', '')}|{row.get('kick_off', '')}"
                    existing_keys.add(key)
        except Exception:
            pass

    candidates = []
    for lv in live_values:
        edge = lv.get('edge', 0)
        conf = lv.get('confidence', 0)
        tier = lv.get('tier', 5)
        minute = lv.get('minute_num', 0)
        try:
            minute = int(str(lv.get('minute', '0')).rstrip("'+"))
        except (ValueError, TypeError):
            minute = 0

        # ── ZLATNA LIVE RULES ──
        if edge < 0.12:          # minimum 12% edge
            continue
        if conf < 0.60:          # minimum 60% confidence
            continue
        if tier not in (3, 4):   # only T3-T4 leagues
            continue
        if minute < 10 or minute > 75:  # sweet spot minutes
            continue

        # Score for live bets
        edge_score = min(edge / 0.20, 1.0) * 35
        conf_score = conf * 25
        tier_score = 20 if tier == 4 else 15  # T4 has more edge
        phase_score = 10 if 20 <= minute <= 65 else 5  # optimal phase
        score = round(edge_score + conf_score + tier_score + phase_score, 1)

        match_str = f"{lv['home']} vs {lv['away']}"
        bet_label = lv.get('bet_label', lv.get('bet', ''))
        key = f"{match_str}|{bet_label}|"
        if key in existing_keys:
            continue

        row = {
            'captured_at': now.strftime('%H:%M:%S'),
            'kick_off': f"LIVE {lv.get('minute', '?')}'",
            'min_to_ko': 'LIVE',
            'match': match_str,
            'bet': bet_label,
            'odds': f"{lv.get('offered_odds', 0):.2f}",
            'opening': f"{lv.get('fair_odds', 0):.2f}",
            'drop_pct': f"{edge:+.1%}",
            'snapshots': f"T{tier}",
            'confidence': f"{conf:.0%}",
            'score': f"{score:.1f}",
            'market_consensus': lv.get('score', ''),
            'type': 'LIVE_VALUE',
            'reason': lv.get('reason', ''),
        }
        candidates.append(row)
        existing_keys.add(key)

    if not candidates:
        return []

    # Sort by score
    candidates.sort(key=lambda r: float(r['score']), reverse=True)

    # Write (append)
    file_exists = tsv_path.exists()
    with open(tsv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=ZLATNA_COLUMNS, delimiter='\t')
        if not file_exists:
            writer.writeheader()
        writer.writerows(candidates)

    return candidates


def _log_cycle(cycle_num: int, num_matches: int, num_signals: int):
    """Log cycle completion to a file for debugging."""
    log_path = DATA_DIR / "tracker_log.txt"
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"Cycle {cycle_num} | {num_matches} matches | "
                    f"{num_signals} signals\n")
    except Exception:
        pass


def run_continuous(watch_list: list[str] | None = None, watch_edge: float | None = None, watch_delta: float | None = None, watch_conf: float | None = None):
    """Run the scrape-analyze-signal loop continuously."""
    init_db()
    cycle = 0
    total_signals = 0
    consecutive_errors = 0

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         ODDS TRACKER — Pre-Match + Live Value Betting        ║
║                                                              ║
║  Pre-match scraping every {SCRAPE_INTERVAL_SECONDS:3d}s from OddsPortal            ║
║  Live in-play scraping + Poisson value betting model         ║
║  ZLATNA PRAVILA: best bets auto-captured                     ║
║  Press Ctrl+C to stop and generate report                    ║
╚══════════════════════════════════════════════════════════════╝
""")

    try:
        while True:
            cycle += 1
            try:
                num_matches, signals = run_one_cycle(
                    cycle,
                    watch_list=watch_list,
                    watch_edge=watch_edge,
                    watch_delta=watch_delta,
                    watch_conf=watch_conf,
                )
                total_signals += len(signals)
                consecutive_errors = 0
            except Exception as e:
                import traceback
                consecutive_errors += 1
                print(f"\n  ⚠⚠⚠ CYCLE {cycle} CRASHED: {e}")
                traceback.print_exc()
                _log_cycle(cycle, -1, -1)
                # Don't crash the whole loop — wait and retry
                if consecutive_errors >= 5:
                    print(f"  🛑 5 consecutive errors — waiting 5 min before retry")
                    time.sleep(300)
                    consecutive_errors = 0

            # Show next scrape time
            next_time = datetime.now() + timedelta(seconds=SCRAPE_INTERVAL_SECONDS)
            print(f"\n⏰ Next scrape at {next_time.strftime('%H:%M:%S')} "
                  f"(in {SCRAPE_INTERVAL_SECONDS}s) — Cycle {cycle} done, "
                  f"{total_signals} total signals so far")
            print(f"{'─'*70}", flush=True)

            time.sleep(SCRAPE_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print(f"\n\n🛑 Stopped after {cycle} cycles. Generating final report...")
        generate_report()

def show_current_signals():
    """Show all signals generated today."""
    init_db()
    signals = get_all_signals_today()

    if not signals:
        print("No signals generated today yet. Run the tracker first!")
        return

    print(f"\n📋 Today's Signals ({len(signals)} total):")
    print(f"{'─'*70}")
    for s in signals:
        print(f"  {s['generated_at'][:19]} | {s['signal_type']:12} | "
              f"Bet {s['recommended_bet']} @ {s['odds_at_signal']:.2f} | "
              f"Conf: {s['confidence']:.0%} | "
              f"{s['home_team']} vs {s['away_team']} ({s['kick_off']})")
        print(f"    {s['reason']}")
        print()


def generate_report():
    """Generate end-of-day analysis report."""
    init_db()
    signals = get_all_signals_today()
    total_snapshots = get_snapshot_count()
    unique_matches = get_unique_match_count()

    # Generate movement summary CSV
    summary_path, summary_count = generate_movement_summary()
    print(f"  [OK] Movement summary saved: {summary_path} ({summary_count} matches)")

    report_time = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    report_path = REPORTS_DIR / f'report_{report_time}.txt'

    lines = [
        "=" * 70,
        f"  END OF DAY REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70,
        "",
        f"  Total Snapshots Collected: {total_snapshots}",
        f"  Unique Matches Tracked:    {unique_matches}",
        f"  Total Signals Generated:   {len(signals)}",
        "",
    ]

    if signals:
        # Group signals by match
        by_match = {}
        for s in signals:
            key = f"{s['home_team']} vs {s['away_team']}"
            if key not in by_match:
                by_match[key] = []
            by_match[key].append(s)

        lines.append("  SIGNALS BY MATCH:")
        lines.append("  " + "─" * 60)
        for match_name, match_signals in by_match.items():
            lines.append(f"\n  📌 {match_name}")
            for s in match_signals:
                lines.append(f"     [{s['signal_type']:12}] Bet {s['recommended_bet']} "
                           f"@ {s['odds_at_signal']:.2f} | Conf: {s['confidence']:.0%}")
                lines.append(f"       ↳ {s['reason']}")

        # Confidence breakdown
        lines.append("\n\n  CONFIDENCE DISTRIBUTION:")
        lines.append("  " + "─" * 60)
        high = [s for s in signals if s['confidence'] >= 0.7]
        med = [s for s in signals if 0.4 <= s['confidence'] < 0.7]
        low = [s for s in signals if s['confidence'] < 0.4]
        lines.append(f"    High (≥70%):  {len(high)} signals")
        lines.append(f"    Medium:       {len(med)} signals")
        lines.append(f"    Low (<40%):   {len(low)} signals")

        # Signal types
        lines.append("\n\n  SIGNAL TYPES:")
        lines.append("  " + "─" * 60)
        types = {}
        for s in signals:
            t = s['signal_type']
            types[t] = types.get(t, 0) + 1
        for t, c in sorted(types.items(), key=lambda x: -x[1]):
            lines.append(f"    {t:15} : {c} signals")

    else:
        lines.append("  No signals generated today.")

    # Also save as CSV for further analysis
    csv_path = REPORTS_DIR / f'signals_{report_time}.csv'
    if signals:
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'generated_at', 'home_team', 'away_team', 'kick_off',
                'signal_type', 'recommended_bet', 'confidence',
                'odds_at_signal', 'reason'
            ], delimiter=';')
            writer.writeheader()
            writer.writerows(signals)
        lines.append(f"\n  📄 Signals CSV: {csv_path}")

    report_text = '\n'.join(lines)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report_text)

    print(report_text)
    print(f"\n  📄 Report saved to: {report_path}")


def _run_live_only():
    """Scrape live in-play matches and show value bets."""
    print(f"\n  Scraping live in-play matches...")
    live_matches = scrape_live_odds(headless=HEADLESS, timeout_ms=PAGE_TIMEOUT_MS)
    if not live_matches:
        print("  No live matches found.")
        return

    save_live_snapshot(live_matches)
    print(f"  Found {len(live_matches)} live matches\n")

    for m in live_matches[:10]:
        print(f"  {m['minute']:>5} | {m['home']:20s} {m['score']} {m['away']:20s} | "
              f"1={m.get('odds_1', '-')} X={m.get('odds_x', '-')} 2={m.get('odds_2', '-')}")

    prematch_data = get_prematch_odds_bulk()
    value_bets = analyze_all_live(live_matches, prematch_data)

    if value_bets:
        print(f"\n  💰 {len(value_bets)} VALUE BETS:\n")
        for vb in value_bets[:20]:
            print(format_value_bet(vb))
            print()
    else:
        print("\n  No value bets found.")


def main():
    parser = argparse.ArgumentParser(description='Odds Tracker — Market Movement Analyzer')
    parser.add_argument('--once', action='store_true', help='Run one cycle only')
    parser.add_argument('--report', action='store_true', help='Generate report')
    parser.add_argument('--signals', action='store_true', help='Show current signals')
    parser.add_argument('--interval', type=int, default=SCRAPE_INTERVAL_SECONDS,
                       help=f'Scrape interval in seconds (default: {SCRAPE_INTERVAL_SECONDS})')
    parser.add_argument('--visible', action='store_true', help='Show browser window')
    parser.add_argument('--signalmap', action='store_true', help='Show today\'s signal map')
    parser.add_argument('--live', action='store_true', help='Scrape live in-play matches only')
    parser.add_argument('--watch', nargs='*', help='Watch specific matches for pre-goal alerts (e.g., "Pouso Alegre vs Democrata GV")')
    parser.add_argument('--watch-edge', type=float, help='Min edge to alert (decimal, e.g., 0.12 = 12%)')
    parser.add_argument('--watch-delta', type=float, help='Min edge jump to alert (decimal, e.g., 0.05 = +5%)')
    parser.add_argument('--watch-conf', type=float, help='Min confidence to alert (decimal, e.g., 0.65 = 65%)')

    args = parser.parse_args()

    if args.interval != SCRAPE_INTERVAL_SECONDS:
        # Override interval
        import odds_tracker.config as cfg
        cfg.SCRAPE_INTERVAL_SECONDS = args.interval

    if args.visible:
        import odds_tracker.config as cfg
        cfg.HEADLESS = False

    if args.report:
        generate_report()
    elif args.signalmap:
        print_signal_map()
    elif args.live:
        init_db()
        _run_live_only()
    elif args.signals:
        show_current_signals()
    elif args.once:
        init_db()
        run_one_cycle(
            1,
            watch_list=args.watch if args.watch else None,
            watch_edge=args.watch_edge,
            watch_delta=args.watch_delta,
            watch_conf=args.watch_conf,
        )
    else:
        run_continuous(
            watch_list=args.watch if args.watch else None,
            watch_edge=args.watch_edge,
            watch_delta=args.watch_delta,
            watch_conf=args.watch_conf,
        )


if __name__ == '__main__':
    main()
