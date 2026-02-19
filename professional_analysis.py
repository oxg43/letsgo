#!/usr/bin/env python3
"""
================================================================================
  PROFESSIONAL MOVEMENT ANALYSIS
  
  Deep analysis of historical betting signals to identify:
  - What's working (high win rate patterns)
  - What's NOT working (losing patterns)
  - Systematic errors
  - Actionable improvements
================================================================================
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import warnings
warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).parent
MOVEMENT_DIR = BASE_DIR / "odds_data" / "movement"
PAPER_TRADES = BASE_DIR / "paper_trades_log.csv"

# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def calc_roi(wins, losses, win_odds, loss_stake=1.0):
    """Calculate ROI from wins/losses"""
    if len(wins) + len(losses) == 0:
        return 0, 0
    profit = sum(wins) - len(losses) * loss_stake
    total_staked = (len(wins) + len(losses)) * loss_stake
    roi = (profit / total_staked * 100) if total_staked > 0 else 0
    return profit, roi

def confidence_interval(win_rate, n, z=1.96):
    """95% confidence interval for win rate"""
    if n == 0:
        return 0, 0
    se = np.sqrt(win_rate * (1 - win_rate) / n)
    return max(0, win_rate - z * se), min(1, win_rate + z * se)

# ═══════════════════════════════════════════════════════════════════════════════
#  LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════════

def load_paper_trades():
    """Load paper trades with results"""
    if not PAPER_TRADES.exists():
        print("  [!] No paper_trades_log.csv found!")
        return pd.DataFrame()
    
    df = pd.read_csv(PAPER_TRADES, sep=";", dtype=str)
    
    # Convert numeric columns
    for col in ['odds', 'opening_odds', 'pct_change', 'confidence', 'edge', 'stake', 'profit']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df['snapshots'] = pd.to_numeric(df['snapshots'], errors='coerce')
    
    # Parse kick_off time
    df['kick_hour'] = df['kick_off'].str.split(':').str[0].astype(float)
    
    # Drop % as positive value
    df['drop_pct'] = abs(df['pct_change'] * 100)
    
    return df


def load_movement_data(days=14):
    """Load movement CSVs for comprehensive analysis"""
    csv_files = sorted(MOVEMENT_DIR.glob("*.csv"))
    cutoff = datetime.now() - timedelta(days=days)
    
    frames = []
    for fp in csv_files:
        try:
            date_str = fp.name[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff.replace(hour=0, minute=0, second=0):
                tmp = pd.read_csv(fp, sep=";", dtype=str)
                tmp["source_file"] = fp.name
                frames.append(tmp)
        except:
            continue
    
    if not frames:
        return pd.DataFrame()
    
    df = pd.concat(frames, ignore_index=True)
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    
    for col in ["odds_1", "odds_x", "odds_2"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    return df


# ═══════════════════════════════════════════════════════════════════════════════
#  PAPER TRADES ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_paper_trades(df):
    """Deep analysis of paper trade results"""
    
    # Filter resolved trades
    resolved = df[df['result'].isin(['win', 'loss'])].copy()
    unresolved = df[~df['result'].isin(['win', 'loss'])]
    
    print("\n" + "="*80)
    print("  PAPER TRADES RÉSUMÉ")
    print("="*80)
    print(f"  Total trades: {len(df)}")
    print(f"  Resolved:     {len(resolved)} ({len(resolved)/len(df)*100:.1f}%)")
    print(f"  Unresolved:   {len(unresolved)}")
    print()
    
    if len(resolved) == 0:
        print("  [!] No resolved trades to analyze!")
        return {}
    
    wins = resolved[resolved['result'] == 'win']
    losses = resolved[resolved['result'] == 'loss']
    
    win_rate = len(wins) / len(resolved)
    total_profit = wins['profit'].sum() - len(losses)  # Assuming stake=1
    roi = total_profit / len(resolved) * 100
    
    ci_low, ci_high = confidence_interval(win_rate, len(resolved))
    
    print(f"  OVERALL PERFORMANCE:")
    print(f"    Wins:     {len(wins)} ({win_rate*100:.1f}%)")
    print(f"    Losses:   {len(losses)} ({(1-win_rate)*100:.1f}%)")
    print(f"    Profit:   {total_profit:+.2f} units")
    print(f"    ROI:      {roi:+.2f}%")
    print(f"    95% CI:   [{ci_low*100:.1f}% - {ci_high*100:.1f}%]")
    print()
    
    # Break-even odds
    breakeven_odds = 1 / win_rate if win_rate > 0 else float('inf')
    print(f"  Break-even implied probability: {win_rate*100:.1f}%")
    print(f"  Break-even odds: {breakeven_odds:.2f}")
    print()
    
    results = {
        'overall_win_rate': win_rate,
        'overall_roi': roi,
        'total_profit': total_profit,
        'n_resolved': len(resolved)
    }
    
    # ─── BY SIGNAL TYPE ───────────────────────────────────────────────────────
    print("  BY SIGNAL TYPE:")
    print("  " + "-"*70)
    for sig_type in resolved['signal_type'].unique():
        subset = resolved[resolved['signal_type'] == sig_type]
        w = (subset['result'] == 'win').sum()
        l = (subset['result'] == 'loss').sum()
        n = w + l
        wr = w / n if n > 0 else 0
        prof = subset[subset['result'] == 'win']['profit'].sum() - l
        r = prof / n * 100 if n > 0 else 0
        
        star = "⭐" if r > 0 else "❌" if r < -10 else ""
        print(f"    {sig_type:20s}: {n:3d} trades | Win: {wr*100:5.1f}% | ROI: {r:+6.2f}% {star}")
        
        results[f'{sig_type}_win_rate'] = wr
        results[f'{sig_type}_roi'] = r
        results[f'{sig_type}_n'] = n
    print()
    
    # ─── BY BET TYPE ──────────────────────────────────────────────────────────
    print("  BY BET TYPE (1 = Home, X = Draw, 2 = Away):")
    print("  " + "-"*70)
    for bet in ['1', '2', 'X']:
        subset = resolved[resolved['bet'] == bet]
        if len(subset) == 0:
            continue
        w = (subset['result'] == 'win').sum()
        l = (subset['result'] == 'loss').sum()
        n = w + l
        wr = w / n if n > 0 else 0
        prof = subset[subset['result'] == 'win']['profit'].sum() - l
        r = prof / n * 100 if n > 0 else 0
        
        star = "⭐" if r > 0 else "❌" if r < -10 else ""
        print(f"    Bet {bet}: {n:3d} trades | Win: {wr*100:5.1f}% | ROI: {r:+6.2f}% {star}")
        
        results[f'bet_{bet}_win_rate'] = wr
        results[f'bet_{bet}_roi'] = r
    print()
    
    # ─── BY ODDS BRACKET ──────────────────────────────────────────────────────
    print("  BY ODDS BRACKET:")
    print("  " + "-"*70)
    
    brackets = [
        (1.0, 1.30, "1.00-1.30 (Heavy Fav)"),
        (1.30, 1.50, "1.30-1.50 (Fav)"),
        (1.50, 1.80, "1.50-1.80 (Slight Fav)"),
        (1.80, 2.10, "1.80-2.10 (Even)"),
        (2.10, 2.50, "2.10-2.50 (Underdog)"),
        (2.50, 3.50, "2.50-3.50 (Big Underdog)"),
        (3.50, 10.0, "3.50+ (Long Shot)")
    ]
    
    for lo, hi, label in brackets:
        subset = resolved[(resolved['odds'] >= lo) & (resolved['odds'] < hi)]
        if len(subset) == 0:
            continue
        w = (subset['result'] == 'win').sum()
        l = (subset['result'] == 'loss').sum()
        n = w + l
        wr = w / n if n > 0 else 0
        prof = subset[subset['result'] == 'win']['profit'].sum() - l
        r = prof / n * 100 if n > 0 else 0
        avg_odds = subset['odds'].mean()
        
        # Expected win rate at fair odds
        expected_wr = 1 / avg_odds
        edge = (wr - expected_wr) * 100
        
        star = "⭐" if edge > 5 else "❌" if edge < -10 else ""
        print(f"    {label}: {n:3d} trades | Win: {wr*100:5.1f}% (exp: {expected_wr*100:.1f}%) | Edge: {edge:+.1f}% | ROI: {r:+.1f}% {star}")
        
        results[f'odds_{lo}_{hi}_win_rate'] = wr
        results[f'odds_{lo}_{hi}_edge'] = edge
    print()
    
    # ─── BY DROP % ────────────────────────────────────────────────────────────
    print("  BY DROP % (size of odds movement):")
    print("  " + "-"*70)
    
    drop_brackets = [
        (0, 6, "0-6% (Small)"),
        (6, 10, "6-10% (Medium)"),
        (10, 15, "10-15% (Strong)"),
        (15, 25, "15-25% (Very Strong)"),
        (25, 100, "25%+ (Extreme)")
    ]
    
    for lo, hi, label in drop_brackets:
        subset = resolved[(resolved['drop_pct'] >= lo) & (resolved['drop_pct'] < hi)]
        if len(subset) == 0:
            continue
        w = (subset['result'] == 'win').sum()
        l = (subset['result'] == 'loss').sum()
        n = w + l
        wr = w / n if n > 0 else 0
        prof = subset[subset['result'] == 'win']['profit'].sum() - l
        r = prof / n * 100 if n > 0 else 0
        
        star = "⭐" if r > 0 else "❌" if r < -10 else ""
        print(f"    {label}: {n:3d} trades | Win: {wr*100:5.1f}% | ROI: {r:+6.2f}% {star}")
        
        results[f'drop_{lo}_{hi}_win_rate'] = wr
        results[f'drop_{lo}_{hi}_roi'] = r
    print()
    
    # ─── BY CONFIDENCE ────────────────────────────────────────────────────────
    print("  BY CONFIDENCE LEVEL:")
    print("  " + "-"*70)
    
    conf_brackets = [
        (0.9, 1.01, "90-100%"),
        (0.8, 0.9, "80-90%"),
        (0.7, 0.8, "70-80%"),
        (0.5, 0.7, "50-70%"),
        (0, 0.5, "<50%")
    ]
    
    for lo, hi, label in conf_brackets:
        subset = resolved[(resolved['confidence'] >= lo) & (resolved['confidence'] < hi)]
        if len(subset) == 0:
            continue
        w = (subset['result'] == 'win').sum()
        l = (subset['result'] == 'loss').sum()
        n = w + l
        wr = w / n if n > 0 else 0
        prof = subset[subset['result'] == 'win']['profit'].sum() - l
        r = prof / n * 100 if n > 0 else 0
        
        star = "⭐" if r > 0 else "❌" if r < -10 else ""
        print(f"    {label}: {n:3d} trades | Win: {wr*100:5.1f}% | ROI: {r:+6.2f}% {star}")
    print()
    
    # ─── BY SNAPSHOTS (data quality) ──────────────────────────────────────────
    print("  BY SNAPSHOT COUNT (data quality):")
    print("  " + "-"*70)
    
    snap_brackets = [
        (0, 50, "<50 snaps (Low data)"),
        (50, 100, "50-100 snaps"),
        (100, 150, "100-150 snaps"),
        (150, 250, "150-250 snaps (High data)")
    ]
    
    for lo, hi, label in snap_brackets:
        subset = resolved[(resolved['snapshots'] >= lo) & (resolved['snapshots'] < hi)]
        if len(subset) == 0:
            continue
        w = (subset['result'] == 'win').sum()
        l = (subset['result'] == 'loss').sum()
        n = w + l
        wr = w / n if n > 0 else 0
        prof = subset[subset['result'] == 'win']['profit'].sum() - l
        r = prof / n * 100 if n > 0 else 0
        
        star = "⭐" if r > 0 else "❌" if r < -10 else ""
        print(f"    {label}: {n:3d} trades | Win: {wr*100:5.1f}% | ROI: {r:+6.2f}% {star}")
    print()
    
    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  LOSING PATTERNS ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_losing_patterns(df):
    """Find common patterns in losing bets"""
    
    resolved = df[df['result'].isin(['win', 'loss'])].copy()
    losses = resolved[resolved['result'] == 'loss'].copy()
    
    if len(losses) == 0:
        print("  No losses to analyze!")
        return
    
    print("\n" + "="*80)
    print("  LOSING PATTERNS DEEP DIVE")
    print("="*80)
    print()
    
    print(f"  Total losses: {len(losses)}")
    print()
    
    # Most common losing characteristics
    print("  TOP LOSING CHARACTERISTICS:")
    print("  " + "-"*70)
    
    # By odds
    median_loss_odds = losses['odds'].median()
    median_win_odds = resolved[resolved['result'] == 'win']['odds'].median()
    print(f"    Median odds (losses): {median_loss_odds:.2f}")
    print(f"    Median odds (wins):   {median_win_odds:.2f}")
    if median_loss_odds > median_win_odds:
        print("    ❌ PROBLEM: Losing on higher odds (underdogs)")
    print()
    
    # By drop %
    median_loss_drop = losses['drop_pct'].median()
    median_win_drop = resolved[resolved['result'] == 'win']['drop_pct'].median()
    print(f"    Median drop (losses): {median_loss_drop:.1f}%")
    print(f"    Median drop (wins):   {median_win_drop:.1f}%")
    print()
    
    # Worst performing matches
    print("  SAMPLE LOSING BETS:")
    print("  " + "-"*70)
    for _, row in losses.head(10).iterrows():
        print(f"    {row['home']} vs {row['away']} | {row['bet']} @ {row['odds']:.2f} | Drop: {row['drop_pct']:.1f}%")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  MOVEMENT DATA QUALITY CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_movement_quality(df):
    """Analyze movement data quality and patterns"""
    
    print("\n" + "="*80)
    print("  MOVEMENT DATA QUALITY CHECK")
    print("="*80)
    print()
    
    print(f"  Total rows: {len(df):,}")
    print(f"  Date range: {df['scraped_at'].min()} to {df['scraped_at'].max()}")
    print()
    
    # Unique matches
    df['match_key'] = df['home'].astype(str) + " vs " + df['away'].astype(str)
    unique_matches = df['match_key'].nunique()
    print(f"  Unique matches tracked: {unique_matches:,}")
    print()
    
    # Average snapshots per match
    snaps_per_match = df.groupby('match_key').size()
    print(f"  Snapshots per match:")
    print(f"    Min:    {snaps_per_match.min()}")
    print(f"    Max:    {snaps_per_match.max()}")
    print(f"    Mean:   {snaps_per_match.mean():.1f}")
    print(f"    Median: {snaps_per_match.median():.1f}")
    print()
    
    # Data freshness
    hours_diff = (datetime.now() - df['scraped_at'].max()).total_seconds() / 3600
    print(f"  Latest data: {hours_diff:.1f} hours old")
    if hours_diff > 6:
        print("    ⚠️ Data might be stale - consider running scraper")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  IMPROVEMENT RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_recommendations(results):
    """Generate actionable improvements based on analysis"""
    
    print("\n" + "="*80)
    print("  🎯 IMPROVEMENT RECOMMENDATIONS")
    print("="*80)
    print()
    
    recommendations = []
    
    # 1. Check overall profitability
    if results.get('overall_roi', 0) < 0:
        recommendations.append({
            'priority': 'HIGH',
            'issue': 'Negative overall ROI',
            'action': 'Tighten criteria - increase min drop % and confidence thresholds'
        })
    
    # 2. Check by odds bracket
    for key, val in results.items():
        if 'odds_' in key and '_edge' in key and val < -10:
            bracket = key.replace('odds_', '').replace('_edge', '').replace('_', '-')
            recommendations.append({
                'priority': 'HIGH',
                'issue': f'Negative edge in odds bracket {bracket}',
                'action': f'Consider excluding or reducing stake for this odds range'
            })
    
    # 3. Check by drop %
    for key, val in results.items():
        if 'drop_' in key and '_roi' in key and val < -10:
            bracket = key.replace('drop_', '').replace('_roi', '').replace('_', '-')
            recommendations.append({
                'priority': 'MEDIUM',
                'issue': f'Negative ROI in drop bracket {bracket}%',
                'action': f'Increase minimum drop threshold'
            })
    
    # 4. Check by bet type
    for bet in ['1', '2', 'X']:
        roi = results.get(f'bet_{bet}_roi', None)
        if roi is not None and roi < -15:
            bet_name = {'1': 'Home', '2': 'Away', 'X': 'Draw'}[bet]
            recommendations.append({
                'priority': 'HIGH',
                'issue': f'Bet type {bet_name} ({bet}) has negative ROI: {roi:.1f}%',
                'action': f'Consider excluding {bet_name} bets or using stricter filters'
            })
    
    # 5. Check by signal type
    for sig in ['STEAM', 'SUSTAINED_DROP', 'MIXED']:
        roi = results.get(f'{sig}_roi', None)
        if roi is not None and roi < -15:
            recommendations.append({
                'priority': 'HIGH',
                'issue': f'Signal type {sig} losing money: {roi:.1f}% ROI',
                'action': f'Disable or severely restrict {sig} signals'
            })
    
    # Sort by priority
    priority_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    recommendations.sort(key=lambda x: priority_order[x['priority']])
    
    if not recommendations:
        print("  ✅ No major issues found! System appears to be performing reasonably.")
        print()
        return
    
    for i, rec in enumerate(recommendations, 1):
        emoji = "🔴" if rec['priority'] == 'HIGH' else "🟡" if rec['priority'] == 'MEDIUM' else "🟢"
        print(f"  {emoji} [{rec['priority']}] {rec['issue']}")
        print(f"     → {rec['action']}")
        print()


# ═══════════════════════════════════════════════════════════════════════════════
#  SUMMARY ACTION PLAN
# ═══════════════════════════════════════════════════════════════════════════════

def print_action_plan():
    """Print concrete action plan"""
    
    print("\n" + "="*80)
    print("  📋 CONCRETE ACTION PLAN")
    print("="*80)
    print()
    
    print("""
  IMMEDIATE FIXES (Do Now):
  ─────────────────────────
  1. ✅ Fixed match_date bug (70% were NaN)
  2. ✅ Added monotonicity filter (skip noisy trends)
  3. ✅ Filter high odds + low confidence
  
  RECOMMENDED TWEAKS (signals.py / signal_monitor.py):
  ─────────────────────────────────────────────────────
  4. Increase MIN_DROP from 5% to 8% for STEAM signals
  5. Require monotonicity >= 0.4 for all signal types
  6. Add odds range filter: only 1.40 - 2.50 for STEAM
  7. Exclude MIXED signals (lowest performance)
  8. Increase MIN_SNAPSHOTS from 50 to 80
  
  DATA QUALITY IMPROVEMENTS:
  ──────────────────────────
  9.  Run scraper more frequently (every 2-3 min vs 5 min)
  10. Track match outcomes for continuous backtesting
  11. Implement A/B testing for parameter changes
  
  RISK MANAGEMENT:
  ────────────────
  12. Reduce stake on underdogs (odds > 2.5)
  13. Implement daily loss limit (-5 units max)
  14. Use Kelly criterion for stake sizing
    """)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n")
    print("╔" + "═"*78 + "╗")
    print("║" + " "*20 + "PROFESSIONAL MOVEMENT ANALYSIS" + " "*27 + "║")
    print("║" + " "*25 + f"{datetime.now():%Y-%m-%d %H:%M:%S}" + " "*30 + "║")
    print("╚" + "═"*78 + "╝")
    
    # Load data
    print("\n  Loading paper trades...")
    paper_df = load_paper_trades()
    
    if len(paper_df) > 0:
        results = analyze_paper_trades(paper_df)
        analyze_losing_patterns(paper_df)
        generate_recommendations(results)
    else:
        results = {}
        print("  [!] No paper trades data!")
    
    # Load movement data
    print("\n  Loading movement data...")
    movement_df = load_movement_data(days=14)
    
    if len(movement_df) > 0:
        analyze_movement_quality(movement_df)
    
    print_action_plan()
    
    print("\n" + "="*80)
    print("  ANALYSIS COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
