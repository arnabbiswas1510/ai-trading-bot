"""Route input transports without changing fingerprinted simulation code."""


class ResearchSourceClient:
    def __init__(self, live, research):
        self.live, self.research = live, research

    def table(self, name):
        if name == "intraday_capture_events":
            return self.research.table(name)
        if name not in {
            "portfolio_positions", "daily_triggers", "trade_history",
            "ibkr_fills", "exit_requests",
        }:
            raise ValueError("Table is outside the research source allowlist.")
        return self.live.table(name)
