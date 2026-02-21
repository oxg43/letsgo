#!/usr/bin/env python3
"""
ONLY_HOME Movement Analysis
"""
import pandas as pd
import numpy as np

df = pd.read_csv('odds_data/signal_map/ONLY_HOME_upcoming.tsv', sep='\t')

print('='*70)
print('  ONLY_HOME MOVEMENT ANALYSIS')
print('='*70)
print()

# Basic stats
print(f'Total signals: {len(df)}')
print()

# Drop % distribution
df['drop_num'] = df['drop_pct'].str.replace('+','').str.replace('%','').astype(float)
print('DROP % DISTRIBUTION:')
print(f'  Min:    {df["drop_num"].min():.1f}%')
print(f'  Max:    {df["drop_num"].max():.1f}%')
print(f'  Mean:   {df["drop_num"].mean():.1f}%')
print(f'  Median: {df["drop_num"].median():.1f}%')
print()

# Strategy breakdown
print('STRATEGY BREAKDOWN:')
s1 = df['strategies'].str.contains('S1').sum()
s2 = df['strategies'].str.contains('S2').sum()
s3 = df['strategies'].str.contains('S3').sum()
combo = df['strategies'].str.contains('COMBO').sum()
print(f'  S1:    {s1} ({s1/len(df)*100:.1f}%)')
print(f'  S2:    {s2} ({s2/len(df)*100:.1f}%)')
print(f'  S3:    {s3} ({s3/len(df)*100:.1f}%)')
print(f'  COMBO: {combo} ({combo/len(df)*100:.1f}%)')
print()

# Odds ranges
print('ODDS DISTRIBUTION:')
print(f'  Min:    {df["odds"].min():.2f}')
print(f'  Max:    {df["odds"].max():.2f}')
print(f'  Mean:   {df["odds"].mean():.2f}')
print(f'  Median: {df["odds"].median():.2f}')
print()
print('  Odds brackets:')
print(f'    < 1.50:    {(df["odds"] < 1.50).sum():3d}')
print(f'    1.50-2.00: {((df["odds"] >= 1.50) & (df["odds"] <= 2.00)).sum():3d}')
print(f'    2.00-2.50: {((df["odds"] > 2.00) & (df["odds"] <= 2.50)).sum():3d}')
print(f'    2.50-3.00: {((df["odds"] > 2.50) & (df["odds"] <= 3.00)).sum():3d}')
print(f'    > 3.00:    {(df["odds"] > 3.00).sum():3d}')
print()

# Monotonicity
print('MONOTONICITY (trend consistency):')
print(f'  < 0.3 (noisy):   {(df["monotonic"] < 0.3).sum():3d}')
print(f'  0.3-0.6:         {((df["monotonic"] >= 0.3) & (df["monotonic"] < 0.6)).sum():3d}')
print(f'  0.6-0.9:         {((df["monotonic"] >= 0.6) & (df["monotonic"] < 0.9)).sum():3d}')
print(f'  >= 0.9 (clean):  {(df["monotonic"] >= 0.9).sum():3d}')
print()

# Spread analysis
print('SPREAD DISTRIBUTION:')
print(f'  < 1.0 (tight):  {(df["spread"] < 1.0).sum():3d}')
print(f'  1.0-2.0:        {((df["spread"] >= 1.0) & (df["spread"] < 2.0)).sum():3d}')
print(f'  2.0-3.0:        {((df["spread"] >= 2.0) & (df["spread"] < 3.0)).sum():3d}')
print(f'  >= 3.0 (wide):  {(df["spread"] >= 3.0).sum():3d}')
print()

# Confidence breakdown
print('CONFIDENCE LEVELS:')
for conf in ['100%', '95%', '85%', '80%', '70%', '65%', '55%', '50%']:
    cnt = (df['confidence'] == conf).sum()
    if cnt > 0:
        print(f'  {conf}: {cnt:3d}')
print()

# Problematic patterns
print('='*70)
print('  POTENTIAL ISSUES / TWEAKS NEEDED')
print('='*70)
print()

# 1. High odds with low confidence
high_odds_low_conf = df[(df['odds'] > 3.0) & (df['confidence'].str.replace('%','').astype(int) < 70)]
print(f'1. HIGH ODDS (>3.0) + LOW CONFIDENCE (<70%): {len(high_odds_low_conf)}')
if len(high_odds_low_conf) > 0:
    print('   -> Ove utakmice imaju niske šanse za prolaz!')
    for _, r in high_odds_low_conf.head(5).iterrows():
        print(f'      {r["match"][:40]} @ {r["odds"]} ({r["confidence"]})')
print()

# 2. Low monotonicity (noisy trends)
noisy = df[df['monotonic'] < 0.3]
print(f'2. NOISY TRENDS (monotonic < 0.3): {len(noisy)}')
if len(noisy) > 0:
    print('   -> Kvote osciliraju gore-dolje, signal nije čist!')
    for _, r in noisy.head(5).iterrows():
        print(f'      {r["match"][:40]} mono:{r["monotonic"]:.2f}')
print()

# 3. Very small drops
small_drops = df[df['drop_num'] < 5]
print(f'3. SMALL DROPS (<5%): {len(small_drops)}')
print('   -> Minimalni movement, slabiji signal')
print()

# 4. Tight spread (less clear favorite)
tight_spread = df[df['spread'] < 1.0]
print(f'4. TIGHT SPREAD (<1.0): {len(tight_spread)}')
if len(tight_spread) > 0:
    print('   -> Home nije jasan favorit!')
    for _, r in tight_spread.head(5).iterrows():
        print(f'      {r["match"][:40]} spread:{r["spread"]:.2f}')
print()

# 5. NaN match dates
nan_dates = df[df['match_date'].isna() | (df['match_date'] == 'nan')]
print(f'5. MISSING MATCH DATES: {len(nan_dates)}')
if len(nan_dates) > 0:
    print('   -> BUG: match_date nije postavljen!')
print()

# Best signals
print('='*70)
print('  TOP 10 STRONGEST SIGNALS (COMBO + high drop + clean trend)')
print('='*70)
print()

# Score each signal
df['score'] = 0
df.loc[df['strategies'].str.contains('COMBO'), 'score'] += 30
df.loc[df['drop_num'] >= 10, 'score'] += 20
df.loc[df['drop_num'] >= 15, 'score'] += 10
df.loc[df['monotonic'] >= 0.7, 'score'] += 15
df.loc[df['monotonic'] >= 0.9, 'score'] += 10
df.loc[(df['odds'] >= 1.5) & (df['odds'] <= 2.0), 'score'] += 10
df.loc[(df['spread'] >= 2.0), 'score'] += 5

top10 = df.nlargest(10, 'score')
for _, r in top10.iterrows():
    print(f'{r["match_date"]} {r["kick_off"]}  {r["match"][:35]:<37} @{r["odds"]:.2f} drop:{r["drop_num"]:.0f}% {r["strategies"]}')
