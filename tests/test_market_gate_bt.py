import pytest

from research.market_gate_bt import score


def test_score_preserves_calendar_year_annualization():
    result = score(
        ["2025-10-03", "2026-10-03"],
        [100.0, 110.0],
        [True, True],
    )
    assert result["total_return"] == pytest.approx(0.10)
    assert result["cagr"] == pytest.approx(1.1 ** (365.25 / 365) - 1)
    assert result["maxdd"] == 0
