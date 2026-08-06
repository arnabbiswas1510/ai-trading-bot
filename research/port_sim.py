"""Portfolio-level sim: 4 slots, chronological, capital velocity matters."""
import datetime, statistics, collections
from breakout_bt import (daily, indicators, find_breakouts, dyn_trail, BASE_STOP,
                         PH_GAIN, PH_TRIG, PH_DUR, EMA_BUF, VOL_SURGE)

MAX_POS = 4

def build(universe):
    """Collect all breakout signals and per-symbol bar data keyed by date."""
    sig = collections.defaultdict(list)   # date -> [(sym, idx)]
    bars, emas, dix = {}, {}, {}
    for sym in universe:
        d = daily(sym)
        if not d: continue
        _,_,_,ema = indicators(d)
        bars[sym], emas[sym] = d, ema
        dix[sym] = {b["date"][:10]: k for k,b in enumerate(d)}
        sma, avgv, rh, _ = indicators(d)
        for i in find_breakouts(d):
            if i+1 >= len(d):
                continue
            # Quality proxy standing in for the live final_score, so that when
            # slots are scarce the sim takes the BEST available candidates rather
            # than an arbitrary subset. Without this, reducing slot count is
            # unfairly penalised. Mirrors the screener's own inputs: volume
            # confirmation, proximity to the 52-week pivot, and 12-week RS.
            c, v = d[i]["close"], d[i]["volume"]
            vol_r  = min(3.0, v / avgv[i]) / 3.0 if avgv[i] else 0
            prox   = min(1.0, c / rh[i]) if rh[i] else 0
            j      = max(0, i - 60)
            rs     = (c / d[j]["close"] - 1) if d[j]["close"] else 0
            score  = 0.40*vol_r + 0.35*prox + 0.25*max(0.0, min(1.0, rs))
            sig[d[i+1]["date"][:10]].append((sym, i+1, score))
    return sig, bars, emas, dix

def simulate(cfg, sig, bars, emas, dix, alldates, track=False):
    open_pos, closed = [], []
    occ = []
    blocked = {}          # sym -> index in alldates before which re-entry is blocked
    slots = cfg.get("slots", MAX_POS)
    for di, day in enumerate(alldates):
        # --- manage open positions ---
        still = []
        for p in open_pos:
            d, ema, ix = bars[p["sym"]], emas[p["sym"]], dix[p["sym"]]
            j = ix.get(day)
            if j is None:
                still.append(p); continue
            bar = d[j]; held = j - p["e"]
            cal = (datetime.date.fromisoformat(day) - p["d0"]).days
            if bar["high"] > p["peak"]:
                p["peak"] = bar["high"]; p["last_peak"] = j
            p["pg"] = max(p["pg"], (p["peak"]/p["entry"]-1)*100)
            if cfg["power_hold"]:
                if p["pg"] >= PH_GAIN and cal <= PH_TRIG: p["ph"] = True
                if p["ph"] and cal > PH_DUR: p["ph"] = False
            lvl = p["peak"]*(1-p["trail"])
            if bar["low"] <= lvl:
                closed.append(((lvl/p["entry"]-1)*100, held, "trail", di))
                blocked[p["sym"]] = di + cfg.get("cool", 0); continue
            c = bar["close"]
            nt = dyn_trail(cfg, (c/p["entry"]-1)*100, cal, p["trail"])
            if nt: p["trail"] = nt
            # Intraday Loss Minimiser (daily proxy): sell on a pullback from the
            # running high once that high is at or above entry. Was previously
            # MISSING from this harness, so the portfolio-level results silently
            # ignored the `minimiser` config key.
            if cfg.get("minimiser") and held >= 2 and not p["ph"]:
                p["rh"] = max(p.get("rh", 0.0), c)
                if p["rh"] >= p["entry"]*0.995 and c <= p["rh"]*(1-cfg["minimiser"]):
                    closed.append(((c/p["entry"]-1)*100, held, "minimiser", di))
                    blocked[p["sym"]] = di + cfg.get("cool", 0); continue
            st = cfg.get("stale")
            if st and not p["ph"] and held >= 7 and (j - p["last_peak"]) >= st:
                closed.append(((c/p["entry"]-1)*100, held, "stale", di))
                blocked[p["sym"]] = di + cfg.get("cool", 0); continue
            if (cfg.get("ema",True) and held >= cfg.get("ema_day",7) and not p["ph"]
                    and ema[j] and c < ema[j]*(1-cfg.get("ema_buf",EMA_BUF))):
                closed.append(((c/p["entry"]-1)*100, held, "ema", di))
                blocked[p["sym"]] = di + cfg.get("cool", 0); continue
            still.append(p)
        open_pos = still
        occ.append(len(open_pos))
        # --- new entries into free slots ---
        lag = cfg.get("entry_lag", 0)
        src = sig.get(alldates[di-lag], []) if lag and di-lag >= 0 else (sig.get(day, []) if not lag else [])
        # Best-first, matching the live buy loop which sorts by final_score desc.
        for sym, e, _sc in sorted(src, key=lambda t: -t[2]):
            if len(open_pos) >= slots: break
            if any(p["sym"]==sym for p in open_pos): continue
            if blocked.get(sym, -1) > di: continue
            d = bars[sym]; ix = dix[sym]
            e = ix.get(day, e)
            if e >= len(d) or not d[e]["open"]: continue
            open_pos.append(dict(sym=sym, e=e, entry=d[e]["open"], peak=d[e]["open"],
                                 last_peak=e, trail=cfg.get("base_stop",BASE_STOP),
                                 pg=0.0, ph=False, rh=0.0, d0=datetime.date.fromisoformat(day)))
    if track:
        return closed, occ
    return closed

def report(res, label, years):
    pcts=[r[0] for r in res]; w=[p for p in pcts if p>0]; l=[p for p in pcts if p<=0]
    aw=statistics.mean(w) if w else 0; al=statistics.mean(l) if l else 0
    # compounded on 1/4 of capital per trade
    eq=1.0
    for p in pcts: eq *= (1 + p/100/SLOTS_USED)
    cagr=(eq**(1/years)-1)*100
    print(f"{label:38}{len(pcts):>6}{statistics.mean(pcts):>+8.2f}{100*len(w)/len(pcts):>6.0f}%"
          f"{aw/abs(al) if al else 0:>8.2f}{statistics.mean([r[1] for r in res]):>7.0f}"
          f"{(eq-1)*100:>+10.1f}%{cagr:>+8.1f}%")

if __name__ == "__main__":
    uni=[x.strip() for x in open('broad_names.txt') if x.strip()]
    sig,bars,emas,dix = build(uni)
    alldates = sorted({dt for s in bars for dt in dix[s]})
    years = (datetime.date.fromisoformat(alldates[-1]) - datetime.date.fromisoformat(alldates[0])).days/365.25
    print(f"universe={len(bars)}  days={len(alldates)}  years={years:.2f}  slots={MAX_POS}\n")
    BASE=dict(profit_tiers=[(50,0.050),(30,0.060),(20,0.065)],time_tiers=[],
              power_hold=True,minimiser=None,ema=True,base_stop=0.07)
    print(f"{'config':38}{'n':>6}{'exp%':>8}{'win':>7}{'payoff':>8}{'hold':>7}{'total':>11}{'CAGR':>8}")
    report(simulate(BASE,sig,bars,emas,dix,alldates),"shipped: no stale exit",years)
    for n in (5,8,10,15,20):
        report(simulate(dict(BASE,stale=n),sig,bars,emas,dix,alldates),f"stale: no new high {n}d",years)
