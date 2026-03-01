import pandas as pd

df = pd.read_csv('output/paper_trades/trades.csv', sep=';')
resolved = df[df['status'] != 'pending']

print('=== DIJAGNOZA STARIH A/B TRADEOVA ===')
print()

# Odds distribution
print('--- Distribucija oddsa ---')
for acc in ['A', 'B']:
    a = resolved[resolved['account'] == acc]
    if len(a) > 0:
        print(f'  {acc}: avg odds {a["odds_at_signal"].mean():.2f}, min {a["odds_at_signal"].min():.2f}, max {a["odds_at_signal"].max():.2f}')

print()
print('--- Win rate po predicted_outcome ---')
for out in resolved['predicted_outcome'].unique():
    o = resolved[resolved['predicted_outcome'] == out]
    wr = len(o[o['profit_loss'] > 0]) / len(o) * 100
    print(f'  {out}: {len(o)} trades, {wr:.1f}% win rate, P/L: {o["profit_loss"].sum():+.0f}')

print()
print('--- Win rate po score_composite ranges ---')
for lo, hi in [(50, 59), (60, 69), (70, 79), (80, 89), (90, 100)]:
    s = resolved[(resolved['score_composite'] >= lo) & (resolved['score_composite'] <= hi)]
    if len(s) > 0:
        wr = len(s[s['profit_loss'] > 0]) / len(s) * 100
        print(f'  Score {lo}-{hi}: {len(s)} trades, {wr:.1f}% win, P/L: {s["profit_loss"].sum():+.0f}')

print()
print('--- Win rate po signal_type ---')
for st in resolved['signal_type'].unique():
    s = resolved[resolved['signal_type'] == st]
    wr = len(s[s['profit_loss'] > 0]) / len(s) * 100
    print(f'  {st}: {len(s)} trades, {wr:.1f}% win, avg odds {s["odds_at_signal"].mean():.2f}')

print()
print('--- Top 10 liga po broju tradeova ---')
for lg, cnt in resolved['league'].value_counts().head(10).items():
    s = resolved[resolved['league'] == lg]
    wr = len(s[s['profit_loss'] > 0]) / len(s) * 100
    print(f'  {lg}: {cnt} trades, {wr:.1f}% win, P/L: {s["profit_loss"].sum():+.0f}')

print()
print('--- Koliko je tradeova na exotic vs non-exotic ---')
EXOTIC = {'China', 'India', 'Indonesia', 'Vietnam', 'Thailand', 'Malaysia',
          'Bangladesh', 'Cambodia', 'Laos', 'Myanmar', 'Singapore',
          'Saudi Arabia', 'UAE', 'Bahrain', 'Qatar', 'Oman', 'Kuwait', 'Jordan',
          'Iraq', 'Libya', 'Algeria', 'Tunisia', 'Morocco', 'Egypt', 'Kenya',
          'Tanzania', 'Uganda', 'Ethiopia', 'Ghana', 'Nigeria', 'Cameroon',
          'South Africa', 'Nicaragua', 'Honduras', 'El Salvador', 'Guatemala',
          'Costa Rica', 'Panama', 'Bolivia', 'Venezuela', 'Peru', 'Ecuador',
          'Colombia', 'Paraguay', 'Fiji'}
exotic = resolved[resolved['country'].isin(EXOTIC)]
non_exotic = resolved[~resolved['country'].isin(EXOTIC)]
if len(exotic) > 0:
    wr_e = len(exotic[exotic['profit_loss'] > 0]) / len(exotic) * 100
    print(f'  Exotic: {len(exotic)} trades, {wr_e:.1f}% win, P/L: {exotic["profit_loss"].sum():+.0f}')
if len(non_exotic) > 0:
    wr_n = len(non_exotic[non_exotic['profit_loss'] > 0]) / len(non_exotic) * 100
    print(f'  Non-exotic: {len(non_exotic)} trades, {wr_n:.1f}% win, P/L: {non_exotic["profit_loss"].sum():+.0f}')

print()
print('--- R7 pending trades ---')
r7 = df[df['account'] == 'R7']
print(f'  R7 trades: {len(r7)} ({len(r7[r7["status"]=="pending"])} pending)')

print()
print('--- Avg stake ---')
print(f'  Avg stake: {resolved["stake_eur"].mean():.1f} EUR')
print(f'  Total staked: {resolved["stake_eur"].sum():.0f} EUR')
print(f'  Total P/L: {resolved["profit_loss"].sum():+.0f} EUR')
print(f'  ROI: {resolved["profit_loss"].sum()/resolved["stake_eur"].sum()*100:+.1f}%')
