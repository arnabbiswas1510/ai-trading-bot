"""Offline Treasury reference transport, validation and snapshot contracts."""

import copy
import hashlib
import json
import ssl
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest

from research import treasury_reference as treasury


def csv_bytes(*rows):
    return ("observation_date,DGS3MO\n" + "\n".join(rows) + "\n").encode()


class Response:
    def __init__(self, raw, headers=None):
        self.raw = raw
        self.headers = headers or {}
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        self.read_sizes.append(limit)
        return self.raw[:limit]


def transport(monkeypatch, *responses):
    calls = []
    remaining = iter(responses)

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            result = next(remaining)
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(treasury, "build_opener", lambda *handlers: Opener())
    return calls


def assert_digest(reference):
    unsigned = {key: value for key, value in reference.items() if key != "sha256"}
    assert reference["sha256"] == hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def test_fetch_freezes_normalized_snapshot_and_raw_provenance(monkeypatch):
    raw = csv_bytes(
        "2025-01-06,4.35", "2024-12-05,4.99", "2025-01-02,4.36",
        "2025-01-03,.", "2025-01-11,4.50",
    )
    calls = transport(monkeypatch, Response(csv_bytes("2024-12-06,4.4")), Response(raw))
    reference = treasury.fetch_reference("2025-01-06", "2025-01-10")
    assert reference["status"] == "available"
    assert reference["source"] == "FRED_DGS3MO"
    assert reference["observations"] == [
        {"date": "2024-12-06", "annual_yield_pct": 4.4},
        {"date": "2025-01-02", "annual_yield_pct": 4.36},
        {"date": "2025-01-06", "annual_yield_pct": 4.35},
    ]
    assert len(calls) == 2
    assert "cosd=2024-12-06&coed=2024-12-31" in calls[0][0].full_url
    assert "cosd=2025-01-01&coed=2025-01-10" in calls[1][0].full_url
    assert all(timeout <= 10 and request.data is None for request, timeout in calls)
    assert reference["documents"][1]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert reference["retrieved_at"].endswith("+00:00")
    assert "point-in-time" in reference["warnings"][0]
    assert_digest(reference)


@pytest.mark.parametrize("raw", [
    b"<feed><malformed",
    b"observation_date,DGS1\n2025-01-02,4.3\n",
    csv_bytes("2025-02-30,4.3"),
    csv_bytes("20250102,4.3"),
    csv_bytes("2025-01-02,nan"),
    csv_bytes("2025-01-02,inf"),
    csv_bytes("2025-01-02,-inf"),
    csv_bytes("2025-01-02,nope"),
    csv_bytes("2025-01-02,4.3", "2025-01-02,4.3"),
    csv_bytes("2025-01-02,4.3", "2025-01-02,4.4"),
    csv_bytes("2025-01-02"),
    csv_bytes("2025-01-02,4.3,unexpected"),
    b"\xff",
])
def test_malformed_or_ambiguous_data_fails_closed(monkeypatch, raw):
    transport(monkeypatch, Response(raw))
    with pytest.raises(treasury.TreasuryUnavailable):
        treasury.fetch_reference("2025-02-01", "2025-02-04")


@pytest.mark.parametrize("header", [None, str(treasury.MAX_RESPONSE_BYTES + 1)])
def test_oversize_response_is_bounded(monkeypatch, header):
    response = Response(
        b"x" * (treasury.MAX_RESPONSE_BYTES + 1),
        {} if header is None else {"Content-Length": header},
    )
    transport(monkeypatch, response)
    with pytest.raises(treasury.TreasuryUnavailable, match="size limit"):
        treasury.fetch_reference("2025-02-01", "2025-02-04")
    assert response.read_sizes == ([] if header else [treasury.MAX_RESPONSE_BYTES + 1])


@pytest.mark.parametrize("error", [
    TimeoutError("secret"),
    URLError("secret"),
    IncompleteRead(b"secret", 50),
    HTTPError(treasury.FEED_URL, 503, "secret", {}, None),
])
def test_transport_failure_is_typed_and_does_not_leak_detail(monkeypatch, error):
    calls = transport(monkeypatch, error)
    with pytest.raises(treasury.TreasuryUnavailable) as caught:
        treasury.fetch_reference("2025-02-01", "2025-02-04")
    assert "secret" not in str(caught.value)
    assert len(calls) == 1


def test_tls_failure_explains_trust_problem_without_disabling_verification(monkeypatch):
    transport(monkeypatch, URLError(ssl.SSLCertVerificationError("secret")))
    with pytest.raises(treasury.TreasuryUnavailable, match="TLS certificate verification failed") as caught:
        treasury.fetch_reference("2025-02-01", "2025-02-04")
    assert "secret" not in str(caught.value)


def test_transport_keeps_urllib_standard_user_agent(monkeypatch):
    calls = transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    treasury._get(treasury.FEED_URL + "?id=DGS3MO")
    request, timeout = calls[0]
    assert request.get_header("User-agent") is None
    assert request.get_header("Accept") == "text/csv"
    assert timeout == treasury.REQUEST_TIMEOUT_SECONDS


def test_redirects_are_not_followed():
    assert treasury._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other") is None


@pytest.mark.parametrize("start,end", [
    ("2025-02-30", "2025-03-01"), ("20250101", "2025-03-01"),
    ("2025-03-02", "2025-03-01"), ("2020-01-01", "2025-01-01"),
    ("0001-01-01", "0001-01-02"),
])
def test_invalid_or_excessive_window_never_fetches(monkeypatch, start, end):
    calls = transport(monkeypatch)
    with pytest.raises(treasury.TreasuryUnavailable):
        treasury.fetch_reference(start, end)
    assert calls == []


def test_no_observations_is_not_zero_risk_free(monkeypatch):
    transport(monkeypatch, Response(csv_bytes("2025-01-02,.")))
    with pytest.raises(treasury.TreasuryUnavailable, match="no observations"):
        treasury.fetch_reference("2025-02-01", "2025-02-04")


def test_extend_preserves_previous_yields_and_documents(monkeypatch):
    transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    previous = treasury.fetch_reference("2025-02-01", "2025-02-04")
    original = copy.deepcopy(previous)
    calls = transport(monkeypatch, Response(csv_bytes(
        "2025-01-02,9.99", "2025-02-04,9.98", "2025-02-05,4.35", "2025-02-07,9.97",
    )))
    extended = treasury.fetch_reference("2025-02-01", "2025-02-06", previous)
    assert previous == original
    assert extended["observations"] == original["observations"] + [
        {"date": "2025-02-05", "annual_yield_pct": 4.35},
    ]
    assert extended["documents"][:1] == original["documents"]
    assert extended["urls"][:1] == original["urls"]
    assert "cosd=2025-02-05" in calls[0][0].full_url
    assert_digest(extended)
    assert extended["sha256"] != previous["sha256"]


def test_same_window_returns_independent_identical_snapshot(monkeypatch):
    transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    previous = treasury.fetch_reference("2025-02-01", "2025-02-04")
    calls = transport(monkeypatch)
    result = treasury.fetch_reference("2025-02-01", "2025-02-04", previous)
    assert result == previous
    result["observations"][0]["annual_yield_pct"] = 0
    assert previous["observations"][0]["annual_yield_pct"] == 4.36
    assert not calls


def test_failed_extension_does_not_damage_previous(monkeypatch):
    transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    previous = treasury.fetch_reference("2025-02-01", "2025-02-04")
    original = copy.deepcopy(previous)
    transport(monkeypatch, TimeoutError())
    with pytest.raises(treasury.TreasuryUnavailable):
        treasury.fetch_reference("2025-02-01", "2025-02-06", previous)
    assert previous == original
    assert_digest(previous)


@pytest.mark.parametrize("reseal", [False, True])
def test_invalid_previous_digest_or_schema_is_rejected(monkeypatch, reseal):
    transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    previous = treasury.fetch_reference("2025-02-01", "2025-02-04")
    previous["source"] = "another-series"
    if reseal:
        treasury._seal(previous)
    calls = transport(monkeypatch)
    with pytest.raises(treasury.TreasuryUnavailable):
        treasury.fetch_reference("2025-02-01", "2025-02-06", previous)
    assert not calls


def test_previous_cannot_expand_backwards(monkeypatch):
    transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    previous = treasury.fetch_reference("2025-02-01", "2025-02-04")
    calls = transport(monkeypatch)
    with pytest.raises(treasury.TreasuryUnavailable, match="forwards"):
        treasury.fetch_reference("2025-01-31", "2025-02-06", previous)
    assert not calls


@pytest.mark.parametrize("mutation", [
    lambda ref: ref["observations"].append(dict(ref["observations"][0])),
    lambda ref: ref["observations"][0].update(date="2025-02-30"),
    lambda ref: ref["observations"][0].update(date="2025-02-05"),
    lambda ref: ref["observations"][0].update(annual_yield_pct=True),
    lambda ref: ref.update(documents=[]),
    lambda ref: ref["documents"][0].update(sha256="not-a-digest"),
])
def test_resealed_invalid_previous_is_still_rejected(monkeypatch, mutation):
    transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    previous = treasury.fetch_reference("2025-02-01", "2025-02-04")
    mutation(previous)
    treasury._seal(previous)
    calls = transport(monkeypatch)
    with pytest.raises(treasury.TreasuryUnavailable):
        treasury.fetch_reference("2025-02-01", "2025-02-06", previous)
    assert not calls


def test_extension_over_year_boundary_keeps_all_raw_source_digests(monkeypatch):
    transport(monkeypatch, Response(csv_bytes("2025-12-29,4.2")))
    previous = treasury.fetch_reference("2025-12-30", "2025-12-30")
    calls = transport(monkeypatch,
                      Response(csv_bytes("2025-12-31,4.25")),
                      Response(csv_bytes("2026-01-02,4.3")))
    extended = treasury.fetch_reference("2025-12-30", "2026-01-03", previous)
    assert len(calls) == 2
    assert len(extended["documents"]) == 3
    assert len(extended["observations"]) == 3
    assert extended["documents"][0] == previous["documents"][0]
    assert_digest(extended)


def test_unavailable_reference_has_frozen_reason_and_no_fake_yield():
    reference = treasury.unavailable_reference("network unavailable", "2025-01-01", "2025-01-05")
    assert reference["status"] == "unavailable"
    assert reference["observations"] == []
    assert reference["error"] == "network unavailable"
    assert reference["source"] == treasury.SOURCE
    assert_digest(reference)


def test_failed_extension_retains_history_and_retry_only_freezes_new_dates(monkeypatch):
    transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    original = treasury.fetch_reference("2025-02-01", "2025-02-04")
    original_copy = copy.deepcopy(original)
    failed = treasury.unavailable_reference(
        "TLS verification failed", "2025-02-01", "2025-02-07", previous=original,
    )
    assert failed["status"] == "unavailable"
    assert failed["error"] == "TLS verification failed"
    assert failed["requested_end"] == "2025-02-07"
    assert failed["last_successful_requested_end"] == "2025-02-04"
    assert failed["observations"] == original["observations"]
    assert failed["documents"] == original["documents"]
    assert failed["urls"] == original["urls"]
    assert original == original_copy
    assert_digest(failed)
    second_failure = treasury.unavailable_reference(
        "Still unavailable", "2025-02-01", "2025-02-10", previous=failed,
    )
    assert second_failure["last_successful_requested_end"] == "2025-02-04"
    assert second_failure["observations"] == original["observations"]
    calls = transport(monkeypatch, Response(csv_bytes(
        "2025-01-02,9.99", "2025-02-05,4.35", "2025-02-07,4.30",
    )))
    recovered = treasury.fetch_reference("2025-02-01", "2025-02-07", second_failure)
    assert recovered["status"] == "available"
    assert recovered["error"] is None
    assert recovered["observations"][0] == original["observations"][0]
    assert len(recovered["observations"]) == 3
    assert "cosd=2025-02-05" in calls[0][0].full_url
    assert "last_successful_requested_end" not in recovered
    assert_digest(recovered)


def test_retry_after_initial_failure_fetches_full_lookback(monkeypatch):
    failed = treasury.unavailable_reference("network unavailable", "2025-02-01", "2025-02-04")
    calls = transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    recovered = treasury.fetch_reference("2025-02-01", "2025-02-04", failed)
    assert recovered["status"] == "available"
    assert "cosd=2025-01-01" in calls[0][0].full_url
    assert_digest(recovered)


def test_invalid_prior_digest_is_not_retained_in_failure(monkeypatch):
    transport(monkeypatch, Response(csv_bytes("2025-01-02,4.36")))
    original = treasury.fetch_reference("2025-02-01", "2025-02-04")
    original["observations"][0]["annual_yield_pct"] = 9.99
    failed = treasury.unavailable_reference(
        "digest failure", "2025-02-01", "2025-02-06", previous=original,
    )
    assert failed["observations"] == []
    assert "digest is invalid" in failed["warnings"][-1]
    assert_digest(failed)
