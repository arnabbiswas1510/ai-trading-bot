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
- Log in with `pambi2478` credentials + current Microsoft Authenticator code

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
- Account name: `IBKR pambi2478`
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
ssh root@192.168.1.50
nano /home/dietpi/docker/trading/.env
```

Add this line (replace with your actual Base32 secret):
```
IBKR_TOTP_SECRET=JBSWY3DPEHPK3PXPJEZS4Y3PNVSSA5DP
```

Save and exit (Ctrl+X → Y → Enter)

### Step 8: Update docker-compose.yml (already coded — just needs to be pushed)
Tell the agent: "I have the TOTP secret — add it to docker-compose and push"

The agent will:
1. Re-add `TWOFA_TOTP_SECRET=${IBKR_TOTP_SECRET}` to docker-compose.yml
2. Commit and push
3. The CD pipeline will deploy the updated config to your server

### Step 9: Restart the gateway
```bash
ssh root@192.168.1.50
cd /home/dietpi/docker/trading
git pull origin main
docker compose stop ib-gateway execution-agent
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

### Step 10: Start execution agent
```bash
docker compose up -d execution-agent
docker logs execution-agent -f
```

Expected first lines:
```
✅ Connected to IBKR Gateway successfully!
💰 Cash balance synced from IBKR: $100,931.54
```

---

## Phase 3 — Verify Unattended Operation

### Weekend reconnect test (optional)
After the first weekend maintenance window (Fri ~11:45 PM ET), check Monday morning:
```bash
docker logs ib-gateway --tail 30
docker logs execution-agent --tail 20
```
If both show normal operation, the TOTP automation is working end-to-end.

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

**"Incorrect 2FA code" in gateway logs:**
- Verify the Base32 secret was copied exactly (no spaces, all caps)
- Check server clock sync: `timedatectl` — TOTP fails if clock is off by >30s
- Fix clock: `timedatectl set-ntp true`

**"Login dialog timeout" in gateway logs:**
- IBKR may be showing an unexpected popup (new account notice, etc.)
- Check VNC at `192.168.1.50:5900` (VNC viewer, password from .env) to see the gateway screen
