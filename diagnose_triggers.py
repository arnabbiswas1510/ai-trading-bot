"""Full state snapshot: momentum triggers + verify MS still in Supabase."""
import os, datetime
from zoneinfo import ZoneInfo

with open('.env') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            parts = line.strip().split('=', 1)
            if len(parts) == 2:
                os.environ[parts[0].strip()] = parts[1].strip()

from supabase import create_client
client = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

tz = ZoneInfo('America/New_York')
lookback = (datetime.datetime.now(tz).date() - datetime.timedelta(days=14)).isoformat()

# 1. Current portfolio_positions
print("=" * 60)
print("CURRENT PORTFOLIO_POSITIONS")
print("=" * 60)
pos = client.table('portfolio_positions').select('*').execute().data or []
if pos:
    for p in pos:
        print(f"  {p['ticker']:8s}  shares={p['shares']:4d}  buy=${p['buy_price']:8.2f}  "
              f"stop=${p['stop_loss']:8.2f}  target=${p['profit_target']:8.2f}  "
              f"source={p.get('buy_source')}  hwm=${p.get('high_water_mark')}")
else:
    print("  (empty!)")

# 2. account_balances
print("\naccount_balances:")
for r in client.table('account_balances').select('*').execute().data or []:
    print(f"  {r['key']} = {r['value']}")

# 3. momentum_triggers
print("\n" + "=" * 60)
print("MOMENTUM_TRIGGERS (last 14 days)")
print("=" * 60)
m = client.table('momentum_triggers').select('*').gte('triggered_at', lookback).order('triggered_at', desc=True).execute().data or []
if m:
    print(f"{'Date':<12}  {'Ticker':<8}  {'VolSurge':<10}  {'PivotDist':<12}  Close")
    for r in m:
        print(f"  {r['triggered_at']:<12}  {r['ticker']:<8}  {str(r['volume_surge'])+'x':<10}  "
              f"{str(r['pivot_distance_pct'])+'%':<12}  ${r['close_price']}")
else:
    print("  (no momentum triggers in last 14 days)")

# 4. trade_history recent
print("\n" + "=" * 60)
print("TRADE_HISTORY (last 10 closed trades)")
print("=" * 60)
h = client.table('trade_history').select('*').order('sell_date', desc=True).limit(10).execute().data or []
for r in h:
    pnl = r.get('profit_loss', 0)
    sign = '+' if pnl >= 0 else ''
    print(f"  {str(r.get('sell_date',''))[:10]}  {r['ticker']:8s}  "
          f"buy=${r['buy_price']:8.2f}  sell=${r['sell_price']:8.2f}  "
          f"PnL={sign}${pnl:,.2f}  reason={r.get('sell_reason','')[:30]}")
