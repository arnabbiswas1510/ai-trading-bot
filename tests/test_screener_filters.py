"""
test_screener_filters.py - Tests for the CAN SLIM fundamental gate in
tv_api_screener.py.

Live trading review found the screener was admitting stocks that are not CAN
SLIM candidates: revenue growth only had to exceed 0, and there was no sector
exclusion, so REITs (EGP, FR), an insurer (TRV) and a bank (WSFS) were bought.
These tests lock in the corrected thresholds and the sector exclusion.

See decisions/2026-08-04_widen-exits-and-tighten-entries.md
"""

import sys
import os
import importlib
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reload(monkeypatch, **env):
    """Re-import the screener module with the given env overrides applied."""
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import tv_api_screener
    return importlib.reload(tv_api_screener)


@pytest.fixture
def screener(monkeypatch):
    mod = _reload(monkeypatch, MIN_REVENUE_GROWTH=None, MIN_ANNUAL_EPS_GROWTH=None,
                  MIN_QUARTERLY_EPS_GROWTH=None, EXCLUDED_SECTORS=None)
    yield mod


class TestDefaultThresholds:

    def test_revenue_growth_is_a_real_growth_bar(self, screener):
        """Regression: was 0, which admitted SWK on 0.6% revenue growth."""
        assert screener.MIN_REVENUE_GROWTH >= 15

    def test_annual_eps_growth_matches_oneil(self, screener):
        """Regression: was 15. O'Neil requires ~25%."""
        assert screener.MIN_ANNUAL_EPS_GROWTH >= 25

    def test_quarterly_eps_growth_unchanged(self, screener):
        assert screener.MIN_QUARTERLY_EPS_GROWTH == 20

    def test_rate_driven_sectors_are_excluded(self, screener):
        for sector in ("Finance", "Real Estate", "Utilities"):
            assert sector in screener.EXCLUDED_SECTORS


class TestEnvTunability:
    """Thresholds must be adjustable without a code change, for A/B and rollback."""

    def test_revenue_growth_override(self, monkeypatch):
        mod = _reload(monkeypatch, MIN_REVENUE_GROWTH="25")
        assert mod.MIN_REVENUE_GROWTH == 25

    def test_annual_eps_override(self, monkeypatch):
        mod = _reload(monkeypatch, MIN_ANNUAL_EPS_GROWTH="40")
        assert mod.MIN_ANNUAL_EPS_GROWTH == 40

    def test_sector_exclusion_can_be_disabled(self, monkeypatch):
        mod = _reload(monkeypatch, EXCLUDED_SECTORS="")
        assert mod.EXCLUDED_SECTORS == []

    def test_sector_exclusion_is_whitespace_tolerant(self, monkeypatch):
        mod = _reload(monkeypatch, EXCLUDED_SECTORS=" Finance , Utilities ")
        assert mod.EXCLUDED_SECTORS == ["Finance", "Utilities"]

    def test_sector_exclusion_drops_empty_entries(self, monkeypatch):
        mod = _reload(monkeypatch, EXCLUDED_SECTORS="Finance,,Utilities,")
        assert mod.EXCLUDED_SECTORS == ["Finance", "Utilities"]
