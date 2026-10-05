"""Bounded public quote acquisition; no broker, storage or strategy dependencies."""

import re


def fmp_symbol(ticker):
    """Translate dotted A/B share classes, not exchange suffixes or broker symbols."""
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("FMP ticker must be a nonempty string")
    match = re.fullmatch(r"([A-Z][A-Z0-9]*)\.([AB])", ticker)
    return "-".join(match.groups()) if match else ticker


def quote_budget_seconds(sample_seconds, max_quote_age):
    return min(30.0, sample_seconds / 2, max_quote_age / 2)


def fetch_quotes(http, api_key, tickers, *, budget, clock, monotonic,
                 endpoint="batch-quote", fallback_reason=None, next_symbol=None,
                 stopped=lambda: False, diagnostic=lambda *args, **kwargs: None):
    """Return raw responses and safe provenance, retaining endpoint mode on failure.

    The budget is cooperative: connect/read timeouts are bounded by remaining
    time, and no response received after the deadline becomes usable evidence.
    """
    deadline = monotonic() + budget
    ordered = list(tickers)
    if next_symbol in ordered:
        start = ordered.index(next_symbol)
        ordered = ordered[start:] + ordered[:start]
    result = {
        "responses": [], "requests": [], "errors": [], "cancelled": False,
        "endpoint_mode": endpoint, "fallback_reason": fallback_reason,
        "endpoints_attempted": [], "request_count": 0, "request_limit": len(ordered) + 1,
        "budget_seconds": budget, "next_symbol": next_symbol,
    }
    provider_symbols = {ticker: fmp_symbol(ticker) for ticker in ordered}
    if len(set(provider_symbols.values())) != len(ordered):
        result["errors"].append({"reason": "ambiguous_provider_symbol"})
        return result
    offset = 0
    while offset < len(ordered):
        if stopped():
            result["cancelled"] = True
            break
        remaining = deadline - monotonic()
        if remaining <= 0 or result["request_count"] >= result["request_limit"]:
            result["errors"].append({"reason": "quote_budget_exhausted",
                                     "unrequested_symbols": ordered[offset:]})
            break
        endpoint = result["endpoint_mode"]
        chunk = ordered[offset:offset + (100 if endpoint == "batch-quote" else 1)]
        symbol_map = {provider_symbols[ticker]: ticker for ticker in chunk}
        params = ({"symbols": ",".join(symbol_map)} if endpoint == "batch-quote"
                  else {"symbol": provider_symbols[chunk[0]]})
        result["request_count"] += 1
        if endpoint not in result["endpoints_attempted"]:
            result["endpoints_attempted"].append(endpoint)
        proof = {"source": "FMP", "endpoint": endpoint, "parameters": params,
                 "requested_at": clock(), "symbol_map": symbol_map}
        result["requests"].append(proof)
        try:
            response = http.get(f"https://financialmodelingprep.com/stable/{endpoint}",
                                params={**params, "apikey": api_key},
                                timeout=(min(3, remaining / 2), min(10, remaining / 2)),
                                allow_redirects=False)
            proof["received_at"] = clock()
            if stopped():
                result["cancelled"] = True
                break
            status = getattr(response, "status_code", None)
            if isinstance(status, int):
                proof["status_code"] = status
            if endpoint == "batch-quote" and status == 402:
                result["endpoint_mode"] = "quote"
                result["fallback_reason"] = "batch_quote_http_402"
                diagnostic("quote_endpoint_selected", level="INFO", context={
                    "operation": "fmp_individual_quote", "reason_code": result["fallback_reason"]})
                continue
            response.raise_for_status()
            if isinstance(status, int) and 300 <= status < 400:
                raise ValueError("FMP quote redirect refused")
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("FMP response is not a quote list")
            if monotonic() >= deadline:
                result["errors"].append({"reason": "quote_budget_exhausted",
                                         "unrecorded_symbols": ordered[offset:]})
                break
        except Exception as exc:
            proof.setdefault("received_at", clock())
            proof["error_type"] = type(exc).__name__
            diagnostic("quote_request_failed", error=exc,
                       context={"operation": f"fmp_{endpoint.replace('-', '_')}"})
            result["errors"].extend({"ticker": t, "reason": "quote_request_failed",
                                     "error_type": type(exc).__name__} for t in chunk)
            offset += len(chunk)
            if endpoint == "quote":
                break
            continue
        result["responses"].append({"rows": rows, "symbols": chunk, "proof": proof})
        offset += len(chunk)
    if ordered:
        result["next_symbol"] = ordered[offset % len(ordered)]
    return result
