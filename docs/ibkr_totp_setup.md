# IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot

## Overview
This guide configures your IBKR live account so the bot can log in automatically
24/7 without requiring manual 2FA intervention — including after weekend maintenance
windows when IBKR forces a disconnect.

**Time required:** ~15 minutes  
**Impact:** No disruption to Microsoft Authenticator — both work in parallel

---

## Phase 1 — Extract the TOTP Base32 Secret from IBKR

### Step 1: Log into IBKR Client Portal
- Go to: https://www.interactivebrokers.com/portal
- Log in with your live-account credentials + current Microsoft Authenticator code

### Step 2: Navigate to Secure Login Settings
- Click your **username/account** in the top right → **Settings**
- Left sidebar → **Security** → **Secure Login System**
- You will see your current 2FA method listed (e.g. "IBKR Key" or "IBKR Mobile")

### Step 3: Re-enroll the Software Token (to reveal the secret)
> ⚠️ This temporarily removes your existing software token.
> You will re-add Microsoft Authenticator during this same process.
> Keep your phone nearby.

- Click **"Change"** or **"Remove/Replace"** next to your current software token
- Select **"Add Authentication"** or **"Software Token"** / **"IBKR Key"**
- IBKR will display a **QR code** for you to scan

### Step 4: Reveal the Base32 Secret — CRITICAL STEP
- **DO NOT** scan the QR code yet
- Look for a link below the QR code that says:
  - **"Can't scan the code?"**
  - **"Enter key manually"**
  - **"Show secret key"**
  - (exact wording varies by IBKR UI version)
- Click it — IBKR reveals the raw Base32 secret key
- It looks like: `JBSWY3DPEHPK3PXPJEZS4Y3PNVSSA5DP` (32 uppercase letters/numbers)
- **Copy and save this key securely** (password manager, encrypted note)

### Step 5: Add to Microsoft Authenticator (same session)
- Open Microsoft Authenticator on your phone
- Tap **+** → **Other account (Google, Facebook, etc.)** or **Work/School account**
- Tap **"Enter code manually"** (instead of scanning QR)
- Account name: `IBKR <your-username>`
- Secret key: paste the Base32 secret you just copied
- Tap **Add**
- Verify a 6-digit code is now showing in Microsoft Authenticator

### Step 6: Complete IBKR enrollment
- Back in the IBKR portal, enter the 6-digit code currently shown in Microsoft Authenticator
- Click **Confirm/Activate**
- ✅ Your Microsoft Authenticator is now re-enrolled AND you have the secret

---

## Phase 2 — Configure the Trading Bot

### Step 7: Add secret to server .env
SSH into your server and add the TOTP secret:
```bash
ssh root@192.168.1.2
nano /home/pom/docker/trading/.env
```

Add this line (replace with your actual Base32 secret):
```
IBKR_TOTP_SECRET=JBSWY3DPEHPK3PXPJEZS4Y3PNVSSA5DP
```

Save and exit (Ctrl+X → Y → Enter)

### Step 8: Deploy the configuration without resuming trading

Apply the delivered patch and push from the operator's machine. Keep
`TRADING_RUNTIME_MODE`, the GitHub repository Actions variable, unset or
`observe`. The pipeline starts `execution-agent` for real protection alongside the
independent observer, shadow worker and dashboard. New real buys require the
dashboard's persistent permission, initially OFF; it does not recreate an already-running
gateway. Apply all three research migrations and configure the cloud watchdog
as described in `docs/intraday_research.md`.
Real secrets remain on the production host, never in the patch.

### Step 9: Restart the gateway
```bash
ssh -p 22 pom@192.168.1.2
cd /home/pom/docker/ai-trading-bot
docker compose --profile live stop execution-agent
docker compose --profile observe stop intraday-observer shadow-worker
docker compose stop ib-gateway
docker compose up -d ib-gateway
```

Wait 90 seconds, then check:
```bash
docker logs ib-gateway --tail 20
```

**Expected log lines indicating success:**
```
IBC: Setting user name
IBC: Setting password
IBC: Handling 2FA challenge
IBC: TOTP code entered successfully
IBC: Login completed
```

### Step 10: Resume observation, not trading
```bash
TRADING_RUNTIME_MODE=observe sh scripts/deploy_runtime.sh
docker logs intraday-observer -f
```

Check **Backtester -> Recorded intraday research** for the observer's recent
heartbeat, snapshots and errors. Check the separate **Real trading control**
panel for the execution agent's recent acknowledgment and broker connectivity.
The agent runs protective exits even with new buys OFF. Starting new real buys
requires the authenticated dashboard switch, not `TRADING_RUNTIME_MODE=live`.
See `docs/trading_control.md`, `docs/intraday_research.md` and
`decisions/2026-10-01_dashboard-live-entry-control.md`.

---

## Phase 3 — Verify Unattended Operation

### Weekend reconnect test (optional)
After the first weekend maintenance window (Fri ~11:45 PM ET), check Monday morning:
```bash
docker logs ib-gateway --tail 30
docker logs intraday-observer --tail 20
```
If both show normal operation, the TOTP automation is working end-to-end.

---

## When the gateway does NOT recover — the loud disconnect alert

This section applies with new real buys either ON or OFF: the execution agent
stays running for real protection. The observer separately reports
connection failures through its logs, persisted capture gaps and dashboard
health; do not mistake an observer heartbeat for active risk management.

If IB Gateway gets stuck (e.g. a login/TOTP loop with `connection error No
Internet connection`, or the API port accepting TCP but never completing the
handshake), the execution agent cannot connect. **While disconnected, none of
the risk machinery runs** — trailing stops, the Prove-It Stop, EMA-21 / plateau
exits, the 15-minute monitoring cycle, and market-open buys are all offline, and
any open positions are unmonitored.

The agent gives autoheal ~18 minutes (6 retries with backoff) to restart the
container. If the gateway is still unreachable after that, it fires a dedicated
Telegram alert:

```
🚨 IBKR DISCONNECTED — RISK MANAGEMENT OFFLINE
```

This alert is deliberately distinct from the generic `TRADING BOT EXCEPTION`
message and names the consequence explicitly (which rules are offline, how many
positions are unmonitored, and whether the market is currently open). It is not
suppressed by unrelated exceptions, and it repeats every **30 minutes** for as
long as the outage lasts (`DISCONNECT_REMINDER_SECONDS` in
`telegram_notifier.py`) — a reminder cadence, not a one-shot.

If you receive it, check the gateway on the prod box and restart it:

```bash
docker logs ib-gateway --tail 40
docker restart ib-gateway
docker logs execution-agent -f   # watch for "Connected to IBKR Gateway successfully!"
```

See `decisions/2026-09-07_loud-ibkr-disconnect-alert.md` for why this replaced
the generic exception alert.

---

## Summary — What Changes and What Doesn't

| Item | Before | After |
|---|---|---|
| IBKR desktop/portal login | Microsoft Authenticator (manual code) | Same ✅ unchanged |
| Bot gateway restart | Manual 2FA required | Fully automated ✅ |
| Weekend recovery | Manual restart needed | Auto-reconnects ✅ |
| Security | Same TOTP standard | Same ✅ |

---

## Troubleshooting

**Research/observer failure when production SSH is unavailable:**

Read Supabase `agent_logs` rows prefixed `[RESEARCH-DIAGNOSTIC]` using the
ordinary operational key. The observer's broker connection/snapshot failures
and the recorder's cloud failures use this independent path; private research
table access is not required. Production containers start through
`research_entrypoint.py` before importing their service code. A queued snapshot
is not a confirmed upload, and a fresh diagnostic heartbeat is not proof of
broker protection. See [research diagnostics](intraday_research.md#diagnose-research-failures-without-production-ssh)
and `decisions/2026-10-03_independent-research-diagnostics.md`.

**"Incorrect 2FA code" in gateway logs:**
- Verify the Base32 secret was copied exactly (no spaces, all caps)
- Check server clock sync: `timedatectl` — TOTP fails if clock is off by >30s
- Fix clock: `timedatectl set-ntp true`

**"Login dialog timeout" in gateway logs:**
- IBKR may be showing an unexpected popup (new account notice, etc.)
- Open `http://192.168.1.2:15800/` on the trusted LAN to see the gateway screen.
  Docker maps host port `15800` to the pinned gateway image's noVNC listener
  on container port `5800`; raw VNC port `5900` is not published.
- A connection reset at that URL can mean a wrong Docker port mapping, not a
  failed IBKR login. Check `docker port ib-gateway 5800/tcp`. A changed mapping
  requires recreating the gateway, not merely restarting it. Normal application
  deployment deliberately does not recreate a running gateway. Recreate it only
  during an authorized maintenance window, keeping new real buys OFF and checking
  broker connectivity and protective monitoring afterward.
- The viewer has no application authentication. Do not expose it to the internet
  or untrusted networks.
