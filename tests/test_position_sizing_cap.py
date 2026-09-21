"""
test_position_sizing_cap.py — the equal-weight per-position ceiling.

Regression cover for the 2026-09-21 oversizing incident. On that day four names
opened in the morning at ~$19k each, then as single slots reopened intraday the
sizing rule `available_cash / remaining_slots` dumped ALL free cash into the one
open slot: MPC was sized `$37,916 / 1 = $36,206` and PSX `$37,184 / 1 = $35,856`
against a $22,306 equal-weight share of the $111,530 account. A routine −2% stop
then lost ~$720 on each instead of the ~$400 an equal-weight position would have.

The fix (`equity_capped_position_size`) caps every new position at
`NetLiquidation / MAX_POSITIONS`. These tests are written to FAIL if that cap is
ever removed or weakened. See decisions/2026-09-21_equity-capped-position-size.md.
"""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import (
    make_supabase_mock, make_ib_mock, make_position, make_trigger,
)
import execution_agent
from execution_agent import equity_capped_position_size as cap


# ── Pure-function unit tests ─────────────────────────────────────────────────

class TestEqualWeightCapPure:

    def test_reproduces_the_2026_09_21_mpc_oversizing(self):
        """The exact numbers from the incident: $37,916 into 1 slot must be
        capped to one equal-weight share of the $111,529.70 account, NOT the
        full free-cash pile."""
        size = cap(available_cash=37_915.98, remaining_slots=1,
                   equity=111_529.70, max_positions=5)
        assert size == pytest.approx(111_529.70 / 5, abs=0.01)   # $22,305.94
        assert size < 37_915.98                                  # cap actually bound

    def test_psx_case_also_capped(self):
        size = cap(available_cash=37_184.40, remaining_slots=1,
                   equity=111_529.70, max_positions=5)
        assert size == pytest.approx(22_305.94, abs=0.01)

    def test_below_cap_is_left_untouched(self):
        """When cash-per-slot is already under the equal-weight share, the base
        allocation is returned unchanged — the cap is a ceiling, not a target."""
        size = cap(available_cash=50_000, remaining_slots=5,
                   equity=100_000, max_positions=5)
        assert size == pytest.approx(10_000)     # 50k/5 = 10k < 20k cap

    def test_exactly_at_cap(self):
        size = cap(available_cash=20_000, remaining_slots=1,
                   equity=100_000, max_positions=5)
        assert size == pytest.approx(20_000)

    def test_above_cap_is_clamped(self):
        size = cap(available_cash=40_000, remaining_slots=1,
                   equity=100_000, max_positions=5)
        assert size == pytest.approx(20_000)     # clamped from 40k to 20k

    def test_equity_unknown_zero_skips_cap_not_blocks_buys(self):
        """equity<=0 means NetLiquidation was unavailable. The cap must be
        SKIPPED (return the base), never applied as a $0 ceiling that would size
        every position to zero shares and block all trading."""
        size = cap(available_cash=40_000, remaining_slots=1,
                   equity=0.0, max_positions=5)
        assert size == pytest.approx(40_000)

    def test_equity_negative_skips_cap(self):
        size = cap(available_cash=40_000, remaining_slots=1,
                   equity=-1.0, max_positions=5)
        assert size == pytest.approx(40_000)

    def test_zero_slots_coerced_to_one(self):
        size = cap(available_cash=10_000, remaining_slots=0,
                   equity=100_000, max_positions=5)
        assert size == pytest.approx(10_000)     # 10k/1, under 20k cap

    def test_negative_cash_floored_to_zero(self):
        size = cap(available_cash=-5_000, remaining_slots=2,
                   equity=100_000, max_positions=5)
        assert size == 0.0

    @pytest.mark.parametrize("cash", [1_000, 25_000, 50_000, 250_000, 1_000_000])
    @pytest.mark.parametrize("slots", [1, 2, 3, 4, 5])
    def test_never_exceeds_equal_weight_over_a_wide_range(self, cash, slots):
        """The core invariant: whenever equity is known, no allocation may EVER
        exceed one equal-weight share, for any cash pile or slot count."""
        equity = 111_529.70
        max_pos = 5
        size = cap(cash, slots, equity, max_pos)
        assert size <= equity / max_pos + 1e-6


# ── End-to-end integration through run_market_open_buys ──────────────────────

def _run_buys_with_equity(ib, supabase_mock, own_cash, net_liq,
                          live_price=100.0, ibkr_price=0.0):
    """Runs the real buy loop with cash AND NetLiquidation injected."""
    with patch("execution_agent.supabase", supabase_mock), \
         patch("execution_agent.get_live_price", return_value=live_price), \
         patch("execution_agent.get_own_cash", return_value=own_cash), \
         patch("execution_agent.get_net_liquidation", return_value=net_liq), \
         patch("execution_agent.get_margin_loan", return_value=0.0), \
         patch("execution_agent.fetch_ibkr_delayed_price",
               return_value=(ibkr_price, "delayed" if ibkr_price else "")), \
         patch("execution_agent.is_market_bullish", return_value=True), \
         patch("execution_agent.notifier"), \
         patch("execution_agent.execute_sell"):
        execution_agent.run_market_open_buys(ib)
    return ib


def _placed_qty(ib):
    assert ib.placeOrder.call_count == 1, "expected exactly one buy order"
    order = ib.placeOrder.call_args[0][1]      # placeOrder(contract, order)
    return int(order.totalQuantity)


class TestEqualWeightCapIntegration:

    def test_replacement_slot_is_capped_to_equal_weight(self):
        """The MPC scenario end-to-end: 4 held, 1 free slot, $37,916 free cash,
        $111,530 equity. The bought position must be sized at the equal-weight
        share, not the whole cash pile."""
        held = ["AAAA", "BBBB", "CCCC", "DDDD"]        # 4 of 5 slots full
        price = 425.95
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("MPCX", close_price=price)],
            portfolio=[make_position(t) for t in held],
        )
        ib = make_ib_mock(symbols=held + ["MPCX"])
        _run_buys_with_equity(ib, supabase, own_cash=37_915.98, net_liq=111_529.70)

        qty = _placed_qty(ib)
        cap_dollars = 111_529.70 / execution_agent.MAX_POSITIONS
        assert qty * price <= cap_dollars                       # never over equal-weight
        # Reserve-adjusted expected count at the cap.
        expected = int((cap_dollars - execution_agent.PRICE_SAFETY_RESERVE) / price)
        assert qty == expected                                   # == 50 shares
        # And provably smaller than the uncapped bug would have produced.
        uncapped = int((37_915.98 - execution_agent.PRICE_SAFETY_RESERVE) / price)
        assert qty < uncapped                                    # 50 < 86

    def test_normal_book_still_buys_uncapped(self):
        """Regression: the cap must not shrink a normally-sized position. 3 held,
        2 free slots, cash-per-slot below the equal-weight share."""
        held = ["AAAA", "BBBB", "CCCC"]
        price = 100.0
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("NEWW", close_price=price)],
            portfolio=[make_position(t) for t in held],
        )
        ib = make_ib_mock(symbols=held + ["NEWW"])
        # 40k / 2 slots = 20k base; cap = 100k/5 = 20k -> equal, uncapped path.
        _run_buys_with_equity(ib, supabase, own_cash=40_000, net_liq=100_000)
        qty = _placed_qty(ib)
        assert qty == int((20_000 - execution_agent.PRICE_SAFETY_RESERVE) / price)

    def test_net_liquidation_unavailable_reconstructs_equity_and_still_caps(self):
        """If IBKR's NetLiquidation read returns 0, equity is reconstructed from
        cash + held market value so the cap still binds — it must NOT fall back
        to the uncapped formula that caused the incident."""
        held = ["AAAA", "BBBB", "CCCC", "DDDD"]
        price = 425.95
        portfolio = [make_position(t) for t in held]
        for p in portfolio:
            p["market_value"] = 22_000.0            # 4 * 22k = 88k held value
        supabase = make_supabase_mock(
            daily_triggers=[make_trigger("MPCX", close_price=price)],
            portfolio=portfolio,
        )
        ib = make_ib_mock(symbols=held + ["MPCX"])
        # net_liq=0 -> reconstruct 37,916 cash + 88,000 held = 125,916 equity.
        _run_buys_with_equity(ib, supabase, own_cash=37_915.98, net_liq=0.0)
        qty = _placed_qty(ib)
        recon_equity = 37_915.98 + 4 * 22_000.0
        cap_dollars = recon_equity / execution_agent.MAX_POSITIONS
        assert qty * price <= cap_dollars
        # Still strictly smaller than the uncapped bug.
        uncapped = int((37_915.98 - execution_agent.PRICE_SAFETY_RESERVE) / price)
        assert qty < uncapped
