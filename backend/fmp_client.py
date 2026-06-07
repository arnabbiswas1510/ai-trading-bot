import os
import requests
import pandas as pd
from database import get_setting

class FMPClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key
        if not self.api_key:
            self.api_key = get_setting("fmp_api_key", "")
        if not self.api_key:
            self.api_key = os.getenv("FMP_API_KEY", "")
            
        # Use stable endpoint base path
        self.base_url = "https://financialmodelingprep.com/stable"

    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 0)

    def _get(self, endpoint: str, params: dict = None) -> list:
        if not self.is_configured():
            raise ValueError("FMP API Key is not configured. Please set it in Settings.")
            
        url = f"{self.base_url}/{endpoint}"
        query_params = {
            "apikey": self.api_key
        }
        if params:
            query_params.update(params)
            
        # Debug FMP URL (sans API key)
        debug_params = {k: v for k, v in query_params.items() if k != "apikey"}
        # print(f"[FMP API] Fetching: {url} with params {debug_params}")
        
        response = requests.get(url, params=query_params, timeout=10)
        
        if response.status_code != 200:
            print(f"FMP API error ({response.status_code}) on {endpoint}: {response.text}")
            return []
            
        try:
            data = response.json()
            if isinstance(data, dict) and "Error Message" in data:
                print(f"FMP API returned error on {endpoint}: {data['Error Message']}")
                return []
            return data if isinstance(data, list) else [data]
        except Exception as e:
            print(f"Error parsing FMP API response on {endpoint}: {e}")
            return []

    def get_quote(self, symbol: str) -> dict:
        """Fetch current price, moving averages, volume, 52w range and shares outstanding using stable endpoint."""
        data = self._get("quote", params={"symbol": symbol})
        if data and len(data) > 0:
            return data[0]
        return {}

    def get_historical_prices(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch historical daily prices and format as pandas DataFrame using stable EOD endpoint."""
        params = {
            "symbol": symbol,
            "from": start_date,
            "to": end_date
        }
        data = self._get("historical-price-eod/full", params=params)
        
        if not data or len(data) == 0:
            return pd.DataFrame()
            
        # The EOD stable endpoint returns a list of daily dictionaries directly
        df = pd.DataFrame(data)
        
        if df.empty or 'date' not in df.columns:
            return pd.DataFrame()
            
        df = df.rename(columns={
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        })
        
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")
        df = df.sort_index()
        return df

    def get_income_statements(self, symbol: str, period: str = "quarter", limit: int = 10) -> list:
        """Fetch quarterly or annual income statements using stable endpoint."""
        params = {
            "symbol": symbol,
            "period": period,
            "limit": limit
        }
        return self._get("income-statement", params=params)

    def get_balance_sheets(self, symbol: str, period: str = "annual", limit: int = 3) -> list:
        """Fetch annual balance sheets using stable endpoint."""
        params = {
            "symbol": symbol,
            "period": period,
            "limit": limit
        }
        return self._get("balance-sheet-statement", params=params)

    def get_institutional_holdings_percentage(self, symbol: str, shares_outstanding: float) -> float:
        """
        Calculate institutional holdings percentage.
        Gracefully falls back to a neutral 50.0% if the endpoint is not available or restricted on the free tier.
        """
        if not shares_outstanding or shares_outstanding <= 0:
            return 0.0
            
        # Try stable/v3 endpoint
        url = f"https://financialmodelingprep.com/api/v3/institutional-holder/{symbol}"
        try:
            response = requests.get(url, params={"apikey": self.api_key}, timeout=5)
            if response.status_code == 200:
                holders = response.json()
                if isinstance(holders, list):
                    total_inst_shares = sum(float(h.get("shares", 0)) for h in holders if h.get("shares"))
                    pct = (total_inst_shares / shares_outstanding) * 100.0
                    return min(100.0, pct)
        except Exception as e:
            print(f"Error calling institutional-holder: {e}")
            
        # Fallback to sensible default (50.0%) if blocked on free tier
        # This keeps CAN SLIM scanner running smoothly
        return 50.0

    def run_screener_watchlist(self, market_cap_more_than: int = 1000000000, volume_more_than: int = 100000, limit: int = 50) -> list:
        """
        Query stable stock-screener to find active US growth equities.
        Gracefully falls back to /stable/most-actives if the company-screener is restricted on the free tier.
        """
        params = {
            "marketCapMoreThan": market_cap_more_than,
            "volumeMoreThan": volume_more_than,
            "isActivelyTrading": "true",
            "country": "US",
            "isEtf": "false",
            "limit": limit
        }
        
        # Try full company-screener first
        data = self._get("company-screener", params=params)
        
        # Check if FMP returned a string containing "Restricted Endpoint" in the list
        is_restricted = False
        if data and len(data) > 0:
            first_elem = data[0]
            if isinstance(first_elem, str) and "Restricted Endpoint" in first_elem:
                is_restricted = True
            elif isinstance(first_elem, dict) and any("Restricted Endpoint" in str(v) for v in first_elem.values()):
                is_restricted = True
                
        if not data or is_restricted:
            print("[FMP Client] company-screener is restricted on this plan. Falling back to '/stable/most-actives'...")
            data = self._get("most-actives")
            
        if not data:
            return []
        
        # Extract symbols
        symbols = [item.get("symbol") for item in data if isinstance(item, dict) and item.get("symbol")]
        return sorted(list(set(symbols)))
