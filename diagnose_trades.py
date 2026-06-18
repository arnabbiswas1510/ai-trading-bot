import os, datetime
from zoneinfo import ZoneInfo

# Load .env
with open('.env') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            parts = line.strip().split('=', 1)
            if len(parts) == 2:
                os.environ[parts[0].strip()] = parts[1].strip()

from supabase import create_client
client = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])

tz = ZoneInfo('America/New_York')
today = datetime.datetime.now(tz).date()
today_str = today.isoformat()
lookback_days = int(os.environ.get('TRIGGER_LOOKBACK_DAYS', 3))
cooling_days  = int(os.environ.get('COOLING_OFF_DAYS', 3))
lookback_str  = (today - datetime.timedelta(days=lookback_days)).isoformat()
cooling_str   = (today - datetime.timedelta(days=cooling_days)).isoformat()
max_pos       = int(os.environ.get('MAX_POSITIONS', 4))

print('=' * 60)
print(f'  TRADE DIAGNOSTIC  --  {today_str}')
print('=' * 60)

# 1. Current portfolio
pos_res  = client.table('portfolio_positions').select('*').execute()
positions = pos_res.data or []
held_tickers = [p['ticker'] for p in positions]
free_slots   = max_pos - len(positions)

print(f'\n[1] OPEN POSITIONS  ({len(positions)}/{max_pos}  |  {free_slots} free slot(s)):')
for p in positions:
    print(f'    {p["ticker"]:<6}  bought {p["buy_date"][:10]}  power_hold={p.get("is_power_hold")}')
if not positions:
    print('    (none)')

# 2. Today's triggers
trig_today_res = client.table('daily_triggers').select('*').gte('triggered_at', today_str).execute()
triggers_today = trig_today_res.data or []
print(f'\n[2] TRIGGERS TODAY  ({len(triggers_today)} found):')
for t in triggers_today:
    print(f'    {t["ticker"]:<6}  vol_surge={t["volume_surge"]}x  pivot_dist={t["pivot_distance_pct"]}%')
if not triggers_today:
    print('    (none — technical screener may not have run today or found no breakouts)')

# 3. Triggers in lookback window
trig_recent_res = client.table('daily_triggers').select('*').gte('triggered_at', lookback_str).execute()
triggers_recent = trig_recent_res.data or []
print(f'\n[3] TRIGGERS IN LAST {lookback_days} DAYS  ({len(triggers_recent)} found, from {lookback_str}):')
for t in triggers_recent:
    flag = ' <-- already held' if t['ticker'] in held_tickers else ''
    print(f'    {t["ticker"]:<6}  date={t["triggered_at"]}{flag}')
if not triggers_recent:
    print('    (none)')

# 4. Cooling-off check
print(f'\n[4] RECENT SELLS  (cooling-off window: last {cooling_days} days from {cooling_str}):')
cool_res = client.table('trade_history').select('ticker,sell_date,sell_reason').gte('sell_date', cooling_str).execute()
cooled_sells = cool_res.data or []
cooled_tickers = [c['ticker'] for c in cooled_sells]
if not cooled_sells:
    print('    (none — cooling-off not a factor today)')
else:
    for c in cooled_sells:
        print(f'    {c["ticker"]:<6}  sold={str(c["sell_date"])[:10]}  reason={c["sell_reason"]}')

# 5. Root cause summary
print('\n[5] ROOT CAUSE ANALYSIS:')
causes = []

if free_slots == 0:
    causes.append('PORTFOLIO FULL: no open slots to buy into.')

if not triggers_recent:
    causes.append(f'NO TRIGGERS: technical screener found no breakouts in the last {lookback_days} days.')
else:
    already_held   = [t for t in triggers_recent if t['ticker'] in held_tickers]
    not_held       = [t for t in triggers_recent if t['ticker'] not in held_tickers]
    cooling_blocked = [t for t in not_held if t['ticker'] in cooled_tickers]
    net_actionable = [t for t in not_held if t['ticker'] not in cooled_tickers]

    if already_held:
        causes.append(f'ALREADY HELD: {[t["ticker"] for t in already_held]} were triggered but already in portfolio.')
    if cooling_blocked:
        causes.append(f'COOLING-OFF: {[t["ticker"] for t in cooling_blocked]} were sold within {cooling_days} days — skipped.')
    if net_actionable and free_slots > 0:
        causes.append(f'WARNING — SHOULD HAVE BOUGHT: {[t["ticker"] for t in net_actionable]}. Was the bot running at 9:30 AM?')
    if not net_actionable and free_slots > 0:
        causes.append('No actionable triggers after filtering (held/cooling-off). No buy was expected.')

if not causes:
    causes.append('No obvious cause found — review bot logs for runtime errors.')

for i, c in enumerate(causes, 1):
    print(f'    {i}. {c}')
print()
