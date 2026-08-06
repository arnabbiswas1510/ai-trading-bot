"""Corrected bootstrap for config comparisons.

THE BUG (boot.py):
    diffs = cagr([choice(pl_e) for _ in pl_e]) - cagr([choice(pl_b) for _ in pl_b])
Each arm is resampled with INDEPENDENT randomness, so the statistic has variance
Var(E) + Var(B) instead of Var(E-B). Because the two arms trade the same names in
the same market over the same 3 years, their returns are highly correlated and
that inflation is severe -> the reported "+/-30pp noise floor".

WHY WE CANNOT SIMPLY PAIR ON TRADES:
Changing an exit rule changes exit dates, which changes slot occupancy, which
changes which later entries are taken. The two arms therefore have DIFFERENT
trade lists -- there is no trade-level correspondence to pair on.

THE FIX -- pair on the CALENDAR axis with a stationary block bootstrap
(Politis & Romano 1994):
  * Both arms are simulated over the identical day grid.
  * We resample BLOCKS OF TRADING DAYS, drawn ONCE per replicate and applied to
    BOTH arms. Same market periods, same randomness -> genuine pairing.
  * Blocks (rather than iid trades) preserve serial correlation, regime
    clustering, and the fact that concurrent positions share market shocks --
    all of which an iid trade bootstrap destroys.
"""
import datetime, random, statistics, collections


def cagr_from(pcts, years, slots):
    eq = 1.0
    for p in pcts:
        eq *= (1 + p / 100.0 / slots)
    return (eq ** (1.0 / years) - 1.0) * 100.0 if eq > 0 else -100.0


def trades_by_day(res, ndays):
    by = collections.defaultdict(list)
    for t in res:
        by[t[3]].append(t[0])
    return by


def stationary_blocks(ndays, mean_len, rng):
    """Draw circular blocks with Geometric(1/mean_len) lengths until >= ndays."""
    blocks, total = [], 0
    while total < ndays:
        s = rng.randrange(ndays)
        L = min(rng.geometric_len(mean_len), ndays)
        blocks.append((s, L))
        total += L
    return blocks


class RNG(random.Random):
    def geometric_len(self, mean_len):
        p = 1.0 / mean_len
        n = 1
        while self.random() > p:
            n += 1
        return n


def paired_block_bootstrap(res_a, res_b, ndays, years, slots,
                           reps=2000, mean_len=60, seed=7):
    """Return (median diff, 5th, 95th, P(a>b)) for CAGR_a - CAGR_b."""
    rng = RNG(seed)
    by_a, by_b = trades_by_day(res_a, ndays), trades_by_day(res_b, ndays)
    diffs = []
    for _ in range(reps):
        blocks = stationary_blocks(ndays, mean_len, rng)
        pa, pb = [], []
        for s, L in blocks:
            for k in range(L):
                d = (s + k) % ndays
                pa.extend(by_a.get(d, ()))
                pb.extend(by_b.get(d, ()))
        diffs.append(cagr_from(pa, years, slots) - cagr_from(pb, years, slots))
    diffs.sort()
    return (statistics.median(diffs), diffs[int(.05 * reps)], diffs[int(.95 * reps)],
            sum(1 for d in diffs if d > 0) / reps * 100)


def unpaired_trade_bootstrap(res_a, res_b, years, slots, reps=2000, seed=7):
    """Reproduces the ORIGINAL (buggy) method, for comparison only."""
    rng = random.Random(seed)
    pa = [t[0] for t in res_a]
    pb = [t[0] for t in res_b]
    diffs = sorted(
        cagr_from([rng.choice(pa) for _ in pa], years, slots)
        - cagr_from([rng.choice(pb) for _ in pb], years, slots)
        for _ in range(reps))
    return (statistics.median(diffs), diffs[int(.05 * reps)], diffs[int(.95 * reps)],
            sum(1 for d in diffs if d > 0) / reps * 100)


def single_ci(res, ndays, years, slots, reps=2000, mean_len=60, seed=7):
    rng = RNG(seed)
    by = trades_by_day(res, ndays)
    vals = []
    for _ in range(reps):
        p = []
        for s, L in stationary_blocks(ndays, mean_len, rng):
            for k in range(L):
                p.extend(by.get((s + k) % ndays, ()))
        vals.append(cagr_from(p, years, slots))
    vals.sort()
    return statistics.median(vals), vals[int(.05 * reps)], vals[int(.95 * reps)]
