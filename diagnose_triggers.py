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
today = datetime.datetime.now(tz).date().isoformat()
lookback = (datetime.datetime.now(tz).date() - datetime.timedelta(days=14)).isoformat()

res = client.table('daily_triggers').select('*').gte('triggered_at', lookback).order('triggered_at', desc=True).execute()
rows = res.data or []

print(f"All daily_triggers in last 14 days  ({len(rows)} rows)  |  Today = {today}")
print("-" * 65)
print(f"{'Date':<12}  {'Ticker':<8}  {'VolSurge':<10}  {'PivotDist':<12}  {'Close'}")
print("-" * 65)
for r in rows:
    marker = "  <-- TODAY" if r['triggered_at'] == today else ""
    print(f"{r['triggered_at']:<12}  {r['ticker']:<8}  {str(r['volume_surge'])+'x':<10}  {str(r['pivot_distance_pct'])+'%':<12}  ${r['close_price']}{marker}")

print()
# Also check positions to flag already-held
pos_res = client.table('portfolio_positions').select('ticker').execute()
held = [p['ticker'] for p in (pos_res.data or [])]
print(f"Currently held: {held if held else '(none)'}")
