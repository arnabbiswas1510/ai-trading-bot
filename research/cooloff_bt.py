"""Historical NON-EQUIVALENT cooling-off (re-entry block) sweep.

Answers one question: does blocking re-entry of a just-sold name for N days
make the PORTFOLIO more money, or does it just idle capital?

Cooling-off is an ENTRY gate, so it is measured at the portfolio level where
capital velocity matters — a slot spent re-buying a falling name it just sold is
a slot NOT spent on a fresh breakout. port_sim.simulate() opens positions before
managing the day's exits, including entry-day risk. Blanket experiments retain
their original trading-session block (cool=0 disables it). Reason-aware rows use
the live inclusive calendar cutoff for losses and an unconditional same-session
guard, including cool=0; profits are eligible on the next session.

CAVEAT (stated so it is never forgotten): port_sim uses the PRE-Prove-It exit
stack (EMA-21 exit, stale exit, profit ladder, power-hold, 10% base trail), not
the live Prove-It Stop. Ranking is a proxy and returns are costless fixed-fraction
compounding, not a cash/share portfolio ledger. Neither cooling-off rankings nor
absolute returns establish the live strategy's performance: exits change which
names and slots are available. Prior results must be rerun after the chronology
and calendar corrections. The historical slot assumption is fixed at five.

Run:  python3 research/cooloff_bt.py            # pass_names.txt (screener-passing)
      python3 research/cooloff_bt.py broad      # broad_names.txt (wider universe)
"""
import datetime, statistics, sys
from port_sim import build, simulate

SLOTS = 5   # historical experimental assumption, not a live configuration read

# The historical exit stack, matched to rerun.py's `F` baseline
# (the noise-floor ADR config) so results are comparable to prior work.
BASE = dict(profit_tiers=[(50, .050), (30, .060), (20, .065)], time_tiers=[],
            power_hold=True, minimiser=None, base_stop=0.10, slots=SLOTS,
            ema=True, stale=10)

COOL_VALUES = [0, 1, 2, 3, 4, 5, 7, 10]


def metrics(closed, years):
    pcts = [r[0] for r in closed]
    if not pcts:
        return None
    w = [p for p in pcts if p > 0]
    l = [p for p in pcts if p <= 0]
    eq = 1.0
    for p in pcts:
        eq *= (1 + p / 100 / SLOTS)          # 1/slots of capital per trade
    cagr = (eq ** (1 / years) - 1) * 100
    return {
        "n": len(pcts),
        "exp": statistics.mean(pcts),
        "win": 100 * len(w) / len(pcts),
        "payoff": (statistics.mean(w) / abs(statistics.mean(l))) if l and w else 0.0,
        "hold": statistics.mean([r[1] for r in closed]),
        "total": (eq - 1) * 100,
        "cagr": cagr,
    }


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "pass"
    fname = "broad_names.txt" if which.startswith("broad") else "pass_names.txt"
    uni = [x.strip() for x in open(fname) if x.strip()]
    sig, bars, emas, dix = build(uni)
    alld = sorted({dt for s in bars for dt in dix[s]})
    years = (datetime.date.fromisoformat(alld[-1])
             - datetime.date.fromisoformat(alld[0])).days / 365.25
    print(f"universe={len(bars)}  days={len(alld)}  years={years:.2f}  "
          f"slots={SLOTS}  file={fname}\n")
    print("HISTORICAL / NON-EQUIVALENT: retired exits, proxy ranking, no trading costs.")
    print("Blanket rows use trading sessions; reason-aware rows use inclusive calendar days.")

    print(f"{'cooling-off':>12}{'n':>6}{'exp%':>8}{'win%':>7}{'payoff':>8}"
          f"{'hold':>7}{'total%':>10}{'CAGR%':>8}{'vs cool=0':>11}")
    base_cagr = None
    for cool in COOL_VALUES:
        closed = simulate(dict(BASE, cool=cool), sig, bars, emas, dix, alld)
        mk = metrics(closed, years)
        if mk is None:
            print(f"{cool:>10}d  (no trades)"); continue
        if base_cagr is None:
            base_cagr = mk["cagr"]
        tag = "" if cool == 0 else f"{mk['cagr']-base_cagr:>+9.1f}pp"
        star = "  <- historical blanket reference" if cool == 3 else ""
        print(f"{cool:>10}d {mk['n']:>6}{mk['exp']:>+8.2f}{mk['win']:>6.0f}%"
              f"{mk['payoff']:>8.2f}{mk['hold']:>7.0f}{mk['total']:>+10.1f}"
              f"{mk['cagr']:>+8.1f}{tag:>11}{star}")

    # ── Reason-aware sweep: LOSS exits block for `cool` days, PROFIT exits block
    #    only the same session. This reproduces the live gate's date semantics,
    #    not its surrounding strategy. Blanket comparisons also change day units.
    print(f"\n{'REASON-AWARE (loss-only calendar block, profit=same-session)':}")
    print(f"{'cooling-off':>12}{'n':>6}{'exp%':>8}{'win%':>7}{'payoff':>8}"
          f"{'hold':>7}{'total%':>10}{'CAGR%':>8}{'vs blanket':>11}")
    for cool in COOL_VALUES:
        blanket = metrics(simulate(dict(BASE, cool=cool), sig, bars, emas, dix, alld), years)
        ra = metrics(simulate(dict(BASE, cool=cool, cool_reason_aware=True),
                              sig, bars, emas, dix, alld), years)
        if ra is None or blanket is None:
            continue
        delta = ra["cagr"] - blanket["cagr"]
        star = "  <- reason-aware gate @ 3 calendar days, historical exits" if cool == 3 else ""
        print(f"{cool:>10}d {ra['n']:>6}{ra['exp']:>+8.2f}{ra['win']:>6.0f}%"
              f"{ra['payoff']:>8.2f}{ra['hold']:>7.0f}{ra['total']:>+10.1f}"
              f"{ra['cagr']:>+8.1f}{delta:>+9.1f}pp{star}")


if __name__ == "__main__":
    main()
