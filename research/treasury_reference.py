"""Frozen daily three-month Treasury yield observations for research.

The explicit reference is Federal Reserve H.15's DGS3MO, served by FRED.
It is the investment-basis annual percentage yield, not a realised bill return.
Historical releases can be revised: retrieval freezes a reproducible snapshot,
not a guarantee that today's history was available at the historical date.
No network error silently changes the reference or substitutes a zero yield.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
import ssl
from datetime import date, datetime, timedelta, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCE = "FRED_DGS3MO"
SERIES_URL = "https://fred.stlouisfed.org/series/DGS3MO"
FEED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
REQUEST_TIMEOUT_SECONDS = 8
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_YEAR_REQUESTS = 3
LOOKBACK_DAYS = 31
_WARNING = (
    "Federal Reserve H.15 DGS3MO via FRED: three-month Treasury constant-maturity "
    "investment-basis annual yield, percent; not a realised Treasury return. "
    "Historical observations may be revised; this frozen snapshot is not a "
    "point-in-time vintage guarantee."
)


class TreasuryUnavailable(ValueError):
    """A trustworthy, bounded Treasury reference could not be obtained."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise TreasuryUnavailable("Treasury dates must use YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise TreasuryUnavailable("Treasury reference contains an invalid date.") from exc


def _window(start_date, end_date):
    start, end = _day(start_date), _day(end_date)
    if start > end:
        raise TreasuryUnavailable("Treasury reference start is after its end.")
    try:
        first = start - timedelta(days=LOOKBACK_DAYS)
    except OverflowError as exc:
        raise TreasuryUnavailable("Treasury lookback is outside the supported date range.") from exc
    if end.year - first.year + 1 > MAX_YEAR_REQUESTS:
        raise TreasuryUnavailable("Treasury reference exceeds the three-calendar-year request limit.")
    return start, end, first


def _digest(reference):
    unsigned = {key: value for key, value in reference.items() if key != "sha256"}
    encoded = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _seal(reference):
    reference["sha256"] = _digest(reference)
    return reference


def unavailable_reference(error, start_date, end_date, previous=None):
    """Record failure without discarding validated, previously frozen history.

    ``requested_*`` describes the failed request; ``last_successful_requested_*``
    bounds the retained snapshot. Failed new dates are never marked as covered.
    """
    _day(start_date)
    _day(end_date)
    reference = {
        "schema_version": 1,
        "status": "unavailable",
        "source": SOURCE,
        "requested_start": start_date,
        "requested_end": end_date,
        "retrieved_at": _now(),
        "observations": [],
        "urls": [],
        "documents": [],
        "error": str(error),
        "warnings": [_WARNING],
    }
    if previous is not None:
        try:
            retained, old_start, old_end = _previous(previous)
            if old_end is not None:
                for key in ("observations", "urls", "documents"):
                    reference[key] = retained[key]
                reference["last_successful_requested_start"] = old_start.isoformat()
                reference["last_successful_requested_end"] = old_end.isoformat()
        except TreasuryUnavailable as exc:
            reference["warnings"].append(f"Previous history could not be retained: {exc}")
    return _seal(reference)


def _get(url):
    # FRED drops responses to the custom product agent; keep urllib's standard agent.
    request = Request(url, headers={"Accept": "text/csv"})
    try:
        # No redirects, credentials, request body, retries or TLS overrides.
        with build_opener(_NoRedirect()).open(
            request, timeout=REQUEST_TIMEOUT_SECONDS
        ) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_RESPONSE_BYTES:
                raise TreasuryUnavailable("Treasury response exceeds the size limit.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except TreasuryUnavailable:
        raise
    except HTTPError as exc:
        raise TreasuryUnavailable(f"Treasury public feed returned HTTP {exc.code}.") from exc
    except (URLError, OSError, HTTPException, ValueError) as exc:
        # Exception strings can include proxy credentials. Persist only the class.
        reason = exc.reason if isinstance(exc, URLError) else exc
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise TreasuryUnavailable(
                "Treasury public feed TLS certificate verification failed; "
                "the trusted CA configuration must be repaired."
            ) from exc
        if isinstance(reason, TimeoutError):
            raise TreasuryUnavailable(
                f"Treasury public feed timed out (request timeout {REQUEST_TIMEOUT_SECONDS}s)."
            ) from exc
        raise TreasuryUnavailable(
            f"Treasury public feed request failed ({type(exc).__name__})."
        ) from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TreasuryUnavailable("Treasury response exceeds the size limit.")
    return raw


def _parse(raw, first, last):
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        if reader.fieldnames != ["observation_date", "DGS3MO"]:
            raise TreasuryUnavailable("Treasury feed is missing the expected DGS3MO series.")
        observations, seen = [], set()
        for row in reader:
            if set(row) != {"observation_date", "DGS3MO"} or None in row.values():
                raise TreasuryUnavailable("Treasury feed contains a malformed observation.")
            day = _day(row["observation_date"])
            if day in seen:
                raise TreasuryUnavailable("Treasury feed contains duplicate observation dates.")
            seen.add(day)
            if row["DGS3MO"] in ("", "."):
                continue
            value = float(row["DGS3MO"])
            if not math.isfinite(value):
                raise TreasuryUnavailable("Treasury feed contains a non-finite yield.")
            if first <= day <= last:
                observations.append({"date": day.isoformat(), "annual_yield_pct": value})
        return sorted(observations, key=lambda row: row["date"])
    except TreasuryUnavailable:
        raise
    except (UnicodeError, csv.Error, TypeError, ValueError) as exc:
        raise TreasuryUnavailable("Treasury feed contains malformed CSV data.") from exc


def _previous(reference):
    try:
        if not isinstance(reference, dict) or reference.get("sha256") != _digest(reference):
            raise TreasuryUnavailable("Previous Treasury reference digest is invalid.")
        if (reference["schema_version"] != 1
                or reference["status"] not in ("available", "unavailable")
                or reference["source"] != SOURCE):
            raise TreasuryUnavailable("Previous Treasury reference has an incompatible source or status.")
        observations = reference["observations"]
        if (reference["status"] == "unavailable" and observations == []
                and reference["documents"] == [] and reference["urls"] == []):
            return copy.deepcopy(reference), None, None
        if not isinstance(observations, list) or not observations:
            raise TreasuryUnavailable("Previous Treasury reference has no observations.")
        if reference["status"] == "unavailable":
            start, end, first = _window(
                reference["last_successful_requested_start"],
                reference["last_successful_requested_end"],
            )
        else:
            start, end, first = _window(reference["requested_start"], reference["requested_end"])
        prior = None
        for row in observations:
            day = _day(row["date"])
            value = row["annual_yield_pct"]
            if (not first <= day <= end or (prior is not None and day <= prior)
                    or isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                raise TreasuryUnavailable("Previous Treasury reference has invalid observations.")
            prior = day
        documents = reference["documents"]
        if not documents or reference["urls"] != list(dict.fromkeys(d["url"] for d in documents)):
            raise TreasuryUnavailable("Previous Treasury reference lacks source provenance.")
        for document in documents:
            if (not document["url"].startswith(FEED_URL + "?")
                    or not re.fullmatch(r"[0-9a-f]{64}", document["sha256"])
                    or not document["retrieved_at"]):
                raise TreasuryUnavailable("Previous Treasury document provenance is invalid.")
        return copy.deepcopy(reference), start, end
    except TreasuryUnavailable:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise TreasuryUnavailable("Previous Treasury reference is malformed.") from exc


def fetch_reference(start_date, end_date, previous=None):
    """Fetch FRED DGS3MO with a 31-day lookback, at most three bounded year GETs.

    An extension retains previous observations and source documents verbatim,
    adding only dates strictly after its previous requested end. It cannot
    backfill/revise an already frozen interval or extend its start backwards.
    On failure raise TreasuryUnavailable without mutating ``previous``; callers
    may retain it for historical results but must mark new coverage unavailable.
    """
    start, end, first = _window(start_date, end_date)
    reference = {
        "schema_version": 1,
        "status": "available",
        "source": SOURCE,
        "requested_start": start_date,
        "requested_end": end_date,
        "retrieved_at": _now(),
        "observations": [],
        "urls": [],
        "documents": [],
        "error": None,
        "warnings": [_WARNING],
    }
    if previous is not None:
        retained, old_start, old_end = _previous(previous)
        if old_end is not None:
            reference = retained
            if start < old_start or end < old_end:
                raise TreasuryUnavailable("A frozen Treasury reference may only extend forwards.")
            # Bound the retained coverage, not any preceding failed request.
            _window(old_start.isoformat(), end_date)
            reference["status"] = "available"
            reference["error"] = None
            reference["requested_start"] = old_start.isoformat()
            reference["requested_end"] = end_date
            reference.pop("last_successful_requested_start", None)
            reference.pop("last_successful_requested_end", None)
            if end == old_end:
                return _seal(reference)
            first = old_end + timedelta(days=1)
            reference["retrieved_at"] = _now()
    for year in range(first.year, end.year + 1):
        lower = max(first, date(year, 1, 1))
        upper = min(end, date(year, 12, 31))
        url = FEED_URL + "?" + urlencode({
            "id": "DGS3MO", "cosd": lower.isoformat(), "coed": upper.isoformat(),
        })
        raw = _get(url)
        retrieved_at = _now()
        observations = _parse(raw, lower, upper)
        reference["observations"].extend(observations)
        reference["documents"].append({
            "url": url,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "retrieved_at": retrieved_at,
        })
        if url not in reference["urls"]:
            reference["urls"].append(url)
    if not reference["observations"]:
        raise TreasuryUnavailable("Treasury feed has no observations in the requested window.")
    return _seal(reference)
