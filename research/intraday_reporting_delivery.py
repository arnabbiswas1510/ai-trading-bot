"""Reporting-only transports and durable, recipient-specific delivery receipts.

At-least-once, not exactly-once: acknowledgement can be lost after Telegram has
accepted a message. No raw HTTP errors, response bodies or credential URLs escape.
GitHub issue bodies use fixed operational templates only, even for private repos.
Account details and performance reports remain private to Supabase and Telegram.
"""
import hashlib
import json
import re

import requests

REPORTS = "intraday_research_reports"
INCIDENTS = "intraday_research_incidents"
RECEIPTS = "intraday_research_delivery_receipts"
STATE = "intraday_research_reporting_state"
CALIBRATIONS = "intraday_research_calibration_artifacts"
WRITE_TABLES = {REPORTS, INCIDENTS, RECEIPTS, CALIBRATIONS}


class ReportingError(RuntimeError):
    """Safe operator-facing error without raw transport details."""


class StorageError(ReportingError):
    pass


def identity(*values):
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def safe_detail(exc):
    return str(exc) if isinstance(exc, ReportingError) else type(exc).__name__


def operational_issue_body(key):
    """GitHub may be public: never publish caller-provided diagnostic/report text."""
    diagnostics = {
        "observer-heartbeat": "Observer heartbeat is missing or stale.",
        "observer-output": "Observer has not persisted recent collection output.",
        "observer-spool": "Observer durable storage is unavailable.",
        "broker-snapshot": "Broker observation is missing, stale, disconnected or incomplete.",
        "quote-coverage": "Quote observations are missing, stale or incomplete.",
        "shadow-heartbeat": "Hypothetical worker heartbeat is missing or stale.",
        "shadow-progress": "Hypothetical decision output is missing, stale or blocked.",
        "database": "Private research database access failed.",
        "notification-delivery": "An approved Telegram recipient did not acknowledge a research notification.",
        "watchdog": "The independent research watchdog could not complete its reporting sweep.",
    }
    if key in diagnostics:
        detail = diagnostics[key]
    elif re.fullmatch(r"report-evidence:(daily|weekly):\d{4}-\d{2}-\d{2}", key):
        detail = "Evidence for this report was rejected or exceeded the reporting limits."
    else:
        raise ReportingError("Unsupported operational GitHub incident identifier")
    return (f"Intraday research operational incident: `{key}`.\n\n{detail}\n\n"
            "Review the GitHub Actions run and private research dashboard. Detailed account data, "
            "positions and hypothetical performance are deliberately excluded from this issue. "
            "Approved Telegram recipients and the private report store receive detailed reports.")


class Store:
    """Small PostgREST adapter; pagination fails rather than truncating evidence."""

    def __init__(self, url, key, http=requests):
        if not url or not key:
            raise StorageError("Private intraday database credentials are missing")
        self.url, self.http = url.rstrip("/") + "/rest/v1/", http
        self.headers = {"apikey": key, "Authorization": "Bearer " + key}

    def request(self, method, path, *, params=None, data=None, prefer=None):
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        try:
            response = self.http.request(method, self.url + path, headers=headers,
                                         params=params, json=data, timeout=(5, 20))
            if response.status_code not in (200, 201, 204, 206):
                raise StorageError(f"Private intraday database HTTP {response.status_code}")
            return None if response.status_code == 204 or not response.content else response.json()
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Private intraday database request failed: " + type(exc).__name__) from None

    def select(self, table, params=None):
        result = self.request("GET", table, params=params or {"select": "*"})
        if not isinstance(result, list):
            raise StorageError("Private intraday database returned an invalid row set")
        return result

    def all(self, table, params=None, max_rows=100000):
        params = {"select": "*", "order": "id", **(params or {})}
        result, size = [], 0
        for offset in range(0, max_rows, 500):
            page = self.select(table, {**params, "offset": offset, "limit": 500})
            size += len(json.dumps(page).encode())
            if size > 64 * 1024 * 1024:
                raise ReportingError("Evidence exceeds 64 MiB; refusing an incomplete report")
            result.extend(page)
            if len(page) < 500:
                return result
        raise ReportingError("Evidence exceeds report row limit; refusing a truncated report")

    def put(self, table, row, *, ignore=False):
        if table not in WRITE_TABLES:
            raise ReportingError("Writes outside reporting tables are forbidden")
        preference = "resolution=ignore-duplicates" if ignore else "resolution=merge-duplicates"
        self.request("POST", table, params={"on_conflict": "id"}, data=row, prefer=preference)

    def rpc(self, name, worker):
        if name not in ("claim_intraday_reporting", "release_intraday_reporting"):
            raise ReportingError("Unsupported reporting RPC")
        return self.request("POST", "rpc/" + name, data={"worker": worker})


class Telegram:
    def __init__(self, token, recipients, http=requests):
        self.token = token
        self.recipients = list(dict.fromkeys(r.strip() for r in recipients if r.strip()))
        self.http = http

    def send(self, recipient, body):
        if not self.token or not self.recipients:
            raise ReportingError("Telegram reporting credentials or recipients are missing")
        try:
            result = self.http.post("https://api.telegram.org/bot" + self.token + "/sendMessage",
                                    json={"chat_id": recipient, "text": body,
                                          "disable_web_page_preview": True}, timeout=(5, 20))
            if result.status_code != 200 or result.json().get("ok") is not True:
                raise ReportingError("Telegram reporting delivery was not acknowledged")
        except ReportingError:
            raise
        except Exception as exc:
            raise ReportingError("Telegram reporting request failed: " + type(exc).__name__) from None


class Issues:
    """An open issue is the durable fallback when Supabase is unreachable."""

    def __init__(self, repo, token, http=requests):
        self.repo, self.token, self.http = repo, token, http

    def request(self, method, path="", data=None, params=None):
        if not self.repo or not self.token:
            raise ReportingError("GitHub issue fallback credentials are missing")
        try:
            result = self.http.request(
                method, f"https://api.github.com/repos/{self.repo}/issues{path}",
                headers={"Authorization": "Bearer " + self.token,
                         "Accept": "application/vnd.github+json"},
                json=data, params=params, timeout=(5, 20))
            if result.status_code not in (200, 201):
                raise ReportingError(f"GitHub issue fallback HTTP {result.status_code}")
            return result.json()
        except ReportingError:
            raise
        except Exception as exc:
            raise ReportingError("GitHub issue fallback failed: " + type(exc).__name__) from None

    def find(self, key):
        operational_issue_body(key)  # Validate before putting an identifier into a public title.
        marker = f"[intraday-research:{key}]"
        for page in range(1, 101):
            rows = self.request("GET", params={"state": "open", "per_page": 100, "page": page})
            for row in rows:
                if "pull_request" not in row and marker in row.get("title", ""):
                    return row
            if len(rows) < 100:
                return None
        raise ReportingError("GitHub issue lookup exceeded pagination limit")

    def ensure(self, key, body):
        return self.find(key) or self.request("POST", data={
            "title": f"Intraday research needs attention [intraday-research:{key}]",
            "body": operational_issue_body(key)})

    def update(self, issue, **fields):
        issue.update(self.request("PATCH", f"/{issue['number']}", data=fields))

    def close(self, key):
        issue = self.find(key)
        if issue:
            self.update(issue, state="closed")


def deliver(store, telegram, notification_id, body, now):
    """Persist each recipient/chunk independently; retry only missing receipts."""
    if not telegram.recipients:
        raise ReportingError("Telegram reporting recipients are missing")
    chunks = [body[i:i + 3500] for i in range(0, len(body), 3500)] or [body]
    receipts = store.select(RECEIPTS, {"select": "id", "notification_id": "eq." + notification_id})
    delivered = {r["id"] for r in receipts}
    failures = []
    for recipient in telegram.recipients:
        for index, chunk in enumerate(chunks):
            key = identity(notification_id, recipient, index)
            if key in delivered:
                continue
            try:
                message = (f"HYPOTHETICAL — NOT real trades. Part {index + 1}/{len(chunks)}\n{chunk}"
                           if len(chunks) > 1 else chunk)
                telegram.send(recipient, message)
            except ReportingError as exc:
                failures.append(safe_detail(exc))
                break
            store.put(RECEIPTS, {"id": key, "notification_id": notification_id,
                                 "recipient_hash": identity(recipient, index),
                                 "delivered_at": now.isoformat()}, ignore=True)
    if failures:
        raise ReportingError(f"Telegram reporting failed for {len(failures)} recipient(s)")


def fallback_alert(issues, telegram, key, body, *, recovery=False):
    """Issue-body receipts allow recipient-specific retries even during DB outage."""
    issue_error = None
    try:
        issue = issues.find(key) if recovery else issues.ensure(key, body)
    except ReportingError as exc:
        issue, issue_error = None, exc
    if recovery and issue is None:
        if issue_error:
            raise ReportingError("Recovery incident lookup failed; recovery notification withheld")
        return
    prior = (issue.get("body") or "") if issue else ""
    prefix = "recovery" if recovery else "alert"
    failures = []
    if not telegram.recipients:
        failures.append("Telegram recipients are missing")
    for recipient in telegram.recipients:
        marker = f"<!-- {prefix}-delivered:{identity(recipient)} -->"
        if marker in prior:
            continue
        try:
            telegram.send(recipient, body)
            if issue:
                prior += "\n" + marker
                issues.update(issue, body=prior)
        except ReportingError as exc:
            failures.append(safe_detail(exc))
    if recovery and issue and not failures and issue_error is None:
        issues.update(issue, state="closed")
    if issue_error or failures:
        raise ReportingError("Fallback incident delivery incomplete; check Telegram and GitHub Actions")
