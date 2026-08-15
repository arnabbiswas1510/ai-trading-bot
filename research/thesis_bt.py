"""Thesis stop + cooling-off backtest.

The thesis stop asks a different question from the (disabled) Intraday Loss
Minimiser. The minimiser required `today_high >= entry * 0.995` — it only fired
on positions that had rallied back to or above entry, i.e. positions that were
WORKING, which is why it halved expectancy.

The thesis stop targets the opposite population: breakouts that never got above
entry at all. A proper breakout works almost immediately; failure to advance is
itself the sell signal.

Trigger (all must hold):
  - held >= THESIS_DAY trading days (grace window for the breakout to act)
  - the position has never closed above entry since entry (no follow-through)
  - current close is more than N x ATR% below entry (volatility-normalised, so a
    4%/day mover like DXCM isn't cut on noise)

Exit is modelled as the ARMED exit, not a market sell at the trigger: a tight
trail placed at the trigger bar that rides any bounce, with a deadline. On daily
bars that is approximated as `max(next_open, trigger_close)` capped by the trail.
"""
import datetime, statistics, sys

from port_sim import build
from breakout_bt import dyn_trail, BASE_STOP, PH_GAIN, PH_TRIG, PH_DUR, EMA_BUF

ATR_W = 14


def atr_pct_series(d):
    """Wilder ATR as % of close, aligned to bar index."""
    n = len(d)
    out = [None] * n
    trs = []
    prev_close = None
    atr = None
    for i, b in enumerate(d):
        h, l, c = b["high"], b["low"], b["close"]
        tr = (h - l) if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        if i == ATR_W - 1:
            atr = sum(trs[:ATR_W]) / ATR_W
        elif i >= ATR_W:
            atr = (atr * (ATR_W - 1) + tr) / ATR_W
        if atr is not None and c:
            out[i] = atr / c * 100.0
        prev_close = c
    return out


def simulate(cfg, sig, bars, emas, dix, alldates, atrs, collect_exits=False):
    open_pos, closed = [], []
    blocked = {}
    slots = cfg.get("slots", 4)
    armed_trail = cfg.get("armed_trail", 0.006)

    for di, day in enumerate(alldates):
        still = []
        for p in open_pos:
            d, ema, ix = bars[p["sym"]], emas[p["sym"]], dix[p["sym"]]
            j = ix.get(day)
            if j is None:
                still.append(p); continue
            bar = d[j]; held = j - p["e"]
            cal = (datetime.date.fromisoformat(day) - p["d0"]).days

            # ── armed exit resolution (models arm_exit(): ride the bounce) ──
            if p.get("armed"):
                p["arm_peak"] = max(p["arm_peak"], bar["high"])
                lvl = p["arm_peak"] * (1 - armed_trail)
                if bar["low"] <= lvl:
                    closed.append(((lvl / p["entry"] - 1) * 100, held, p["arm_reason"], di))
                    blocked[p["sym"]] = di + cfg.get("cool", 0); continue
                if held - p["armed_at"] >= cfg.get("arm_deadline_days", 1):
                    closed.append(((bar["close"] / p["entry"] - 1) * 100, held, p["arm_reason"], di))
                    blocked[p["sym"]] = di + cfg.get("cool", 0); continue
                still.append(p); continue

            if bar["high"] > p["peak"]:
                p["peak"] = bar["high"]; p["last_peak"] = j
            p["pg"] = max(p["pg"], (p["peak"] / p["entry"] - 1) * 100)
            if cfg.get("power_hold", True):
                if p["pg"] >= PH_GAIN and cal <= PH_TRIG: p["ph"] = True
                if p["ph"] and cal > PH_DUR: p["ph"] = False

            lvl = p["peak"] * (1 - p["trail"])
            if bar["low"] <= lvl:
                closed.append(((lvl / p["entry"] - 1) * 100, held, "trail", di))
                blocked[p["sym"]] = di + cfg.get("cool", 0); continue

            c = bar["close"]
            # Follow-through latch. Default "close" is the shipped design and the
            # definition the +18.8 dCAGR result in decisions/2026-08-09_thesis-stop.md
            # was measured on. "high" models the production FALLBACK that is active
            # while migrations/add_closed_above_entry.sql is unapplied (an intraday
            # poke disarms the rule). "none" removes the gate entirely.
            latch = cfg.get("latch", "close")
            if latch == "close":
                if c > p["entry"]:
                    p["closed_above"] = True
            elif latch == "high":
                if bar["high"] > p["entry"]:
                    p["closed_above"] = True
            elif latch == "close_margin":
                # Meaningful follow-through: close above entry by a fraction of ATR,
                # so a close one tick above entry does not disarm the rule.
                m = cfg.get("latch_margin_atr", 0.25) * (p["atr0"] or 0.0)
                if (c / p["entry"] - 1) * 100 > m:
                    p["closed_above"] = True
            elif latch == "none":
                pass
            else:
                raise ValueError(f"unknown latch {latch!r}")

            nt = dyn_trail(cfg, (c / p["entry"] - 1) * 100, cal, p["trail"])
            if nt: p["trail"] = nt

            # ── THESIS STOP ────────────────────────────────────────────────
            tm = cfg.get("thesis_atr")
            if (tm is not None and not p["ph"]
                    and held >= cfg.get("thesis_day", 2)
                    and held <= cfg.get("thesis_last_day", 5)
                    and not p["closed_above"]):
                a = p["atr0"]
                # Optional ceiling on the volatility-normalised threshold. Without
                # it a very high-ATR name is effectively exempt: DELL at 7.6%/day
                # gets a -7.6% thesis threshold, wider than most names' trailing
                # stop, so the "cut it early while it is cheap" intent is lost.
                thr = tm * a if a else None
                cap = cfg.get("thesis_cap_pct")
                if thr is not None and cap:
                    thr = min(thr, cap)
                if thr and (c / p["entry"] - 1) * 100 <= -thr:
                    if cfg.get("thesis_armed", True):
                        p["armed"] = True; p["armed_at"] = held
                        p["arm_peak"] = bar["high"]; p["arm_reason"] = "thesis"
                        still.append(p); continue
                    closed.append(((c / p["entry"] - 1) * 100, held, "thesis", di))
                    blocked[p["sym"]] = di + cfg.get("cool", 0); continue

            st = cfg.get("stale")
            if st and not p["ph"] and held >= 7 and (j - p["last_peak"]) >= st:
                closed.append(((c / p["entry"] - 1) * 100, held, "stale", di))
                blocked[p["sym"]] = di + cfg.get("cool", 0); continue

            if (cfg.get("ema", True) and held >= 7 and not p["ph"]
                    and ema[j] and c < ema[j] * (1 - EMA_BUF)):
                closed.append(((c / p["entry"] - 1) * 100, held, "ema", di))
                blocked[p["sym"]] = di + cfg.get("cool", 0); continue

            still.append(p)
        open_pos = still

        for sym, e, _sc in sorted(sig.get(day, []), key=lambda t: -t[2]):
            if len(open_pos) >= slots: break
            if any(p["sym"] == sym for p in open_pos): continue
            if blocked.get(sym, -1) > di: continue
            d = bars[sym]; ix = dix[sym]
            e = ix.get(day, e)
            if e >= len(d) or not d[e]["open"]: continue
            a0 = atrs[sym][e - 1] if e > 0 else None
            open_pos.append(dict(sym=sym, e=e, entry=d[e]["open"], peak=d[e]["open"],
                                 last_peak=e, trail=cfg.get("base_stop", BASE_STOP),
                                 pg=0.0, ph=False, closed_above=False, armed=False,
                                 armed_at=0, arm_peak=0.0, arm_reason="",
                                 atr0=a0, d0=datetime.date.fromisoformat(day)))
    return closed


def stats(res, years, slots=4):
    p = [x[0] for x in res]
    if not p:
        return dict(n=0, exp=0, payoff=0, cagr=0, big20=0, avg_loss=0, worst=0)
    w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
    aw = statistics.mean(w) if w else 0
    al = statistics.mean(l) if l else 0
    eq = 1.0
    for r in p: eq *= (1 + r / 100 / slots)
    return dict(n=len(p), exp=statistics.mean(p),
                payoff=aw / abs(al) if al else 0,
                cagr=(eq ** (1 / years) - 1) * 100,
                big20=100 * sum(1 for r in p if r >= 20) / len(p),
                avg_loss=al, worst=min(p))


def load(fname):
    uni = [x.strip() for x in open(fname) if x.strip()]
    sig, bars, emas, dix = build(uni)
    atrs = {s: atr_pct_series(bars[s]) for s in bars}
    alld = sorted({dt for s in bars for dt in dix[s]})
    years = (datetime.date.fromisoformat(alld[-1]) - datetime.date.fromisoformat(alld[0])).days / 365.25
    return sig, bars, emas, dix, alld, atrs, years


BASE = dict(profit_tiers=[(50, .050), (30, .060), (20, .065)], time_tiers=[],
            power_hold=True, base_stop=0.10, cool=7, slots=4, ema=True, stale=10,
            thesis_atr=None)

if __name__ == "__main__":
    hdr = f"{'config':34}{'n':>5}{'exp%':>8}{'payoff':>8}{'avgloss':>9}{'big20':>7}{'CAGR':>8}"
    for fname, label in [("broad_names.txt", "BROAD"), ("pass_names.txt", "PASS")]:
        sig, bars, emas, dix, alld, atrs, years = load(fname)
        print(f"\n===== {label}  symbols={len(bars)}  years={years:.2f} =====")
        print(hdr)
        variants = [("baseline (no thesis stop)", dict(BASE))]
        for mult in (0.75, 1.0, 1.25, 1.5, 2.0):
            for day in (2, 3):
                variants.append((f"thesis {mult}xATR from day{day}",
                                 dict(BASE, thesis_atr=mult, thesis_day=day)))
        variants.append(("thesis 1.0x d2 MARKET sell",
                         dict(BASE, thesis_atr=1.0, thesis_day=2, thesis_armed=False)))
        for name, cfg in variants:
            s = stats(simulate(cfg, sig, bars, emas, dix, alld, atrs), years)
            print(f"{name:34}{s['n']:>5}{s['exp']:>+8.2f}{s['payoff']:>8.2f}"
                  f"{s['avg_loss']:>+9.2f}{s['big20']:>6.1f}%{s['cagr']:>+8.1f}")
