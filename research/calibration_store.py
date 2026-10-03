"""Private durable research inbox. Every state change and audit event commits together."""
from __future__ import annotations

import hashlib
import json
import uuid


class StoreUnavailable(RuntimeError):
    pass


class Conflict(ValueError):
    pass


class ValidationError(ValueError):
    pass


def artifact_digest(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError("Expected finite JSON.") from exc
    if len(raw) > 32 * 1024 * 1024:
        raise ValidationError("Research artifact exceeds 32 MiB.")
    return hashlib.sha256(raw).hexdigest()


def _revision(value):
    if type(value) is not int or value < 0:
        raise ValidationError("expected_revision must be a nonnegative integer.")
    return value


def _text(value, name, maximum=200):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValidationError(f"Invalid {name}.")
    return value


class CalibrationStore:
    def __init__(self, client):
        self.client = client

    def _query(self, query):
        try:
            return query.execute().data
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == "P4090":
                raise Conflict("Research state changed; refresh before retrying.") from exc
            if code == "P4000":
                raise ValidationError("Invalid research state transition or evidence.") from exc
            raise StoreUnavailable("Research inbox storage is unavailable.") from exc

    def _rpc(self, operation, payload):
        result = self._query(self.client.rpc("intraday_calibration_mutate", {
            "p_operation": operation, "p_payload": payload,
        }))
        if result is None:
            raise StoreUnavailable("Research inbox write was not confirmed.")
        return result

    def settings(self):
        rows = self._query(self.client.table("intraday_calibration_settings")
                           .select("revision,value").eq("id", "default").limit(1))
        if not rows:
            raise StoreUnavailable("Research settings are missing; apply the calibration migration.")
        return rows[0]

    def update_settings(self, expected_revision, value):
        from research.auto_calibration import validate_settings
        return self._rpc("settings", {"expected_revision": _revision(expected_revision),
                                     "value": validate_settings(value)})

    def list_proposals(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValidationError("Proposal limit must be between 1 and 200.")
        return self._query(self.client.table("intraday_calibration_proposals")
                           .select("id,kind,title,parent_id,status,revision,created_at,updated_at,"
                                   "artifact_sha256,request,approval")
                           .order("created_at", desc=True).limit(limit))

    def research_queue(self):
        rows = self._query(self.client.table("intraday_calibration_proposals")
                           .select("id,kind,title,parent_id,status,revision,created_at,updated_at,"
                                   "artifact_sha256,request,approval")
                           .in_("status", ["evaluating", "deferred", "investigation_approved"])
                           .order("created_at").order("id").limit(201))
        if len(rows) > 200:
            raise StoreUnavailable("Research queue exceeds 200 entries; operator review is required.")
        return rows

    def risk_retry_queue(self):
        return self._query(self.client.table("intraday_calibration_proposals")
                           .select("id").in_("status", ["evaluating", "ready", "no_change"])
                           .eq("artifact->risk_analytics->>retry_pending", "true")
                           .order("updated_at").order("id").limit(1))

    def has_proposal(self, identifier):
        rows = self._query(self.client.table("intraday_calibration_proposals").select("id")
                           .eq("id", _text(identifier, "proposal id")).limit(1))
        return bool(rows)

    def proposal(self, identifier):
        rows = self._query(self.client.table("intraday_calibration_proposals").select("*")
                           .eq("id", _text(identifier, "proposal id")).limit(1))
        if not rows:
            raise ValidationError("Proposal not found.")
        row = rows[0]
        row.pop("initial_content", None)
        return row

    def events(self, proposal_id=None, pending_notifications=False):
        query = self.client.table("intraday_calibration_events").select("*")
        if proposal_id is not None:
            query = query.eq("proposal_id", _text(proposal_id, "proposal id"))
        if pending_notifications:
            query = query.is_("notified_at", "null")
        return self._query(query.order("created_at", desc=not pending_notifications).limit(200))

    def deferred_status(self, identifier):
        rows = self._query(self.client.table("intraday_calibration_events").select("data")
                           .eq("proposal_id", _text(identifier, "proposal id")).eq("event", "defer")
                           .order("created_at", desc=True).limit(1))
        return rows[0]["data"].get("previous_status") if rows else None

    def _new_proposal(self, row):
        allowed = {"id", "kind", "title", "parent_id", "status", "artifact",
                   "artifact_sha256", "request"}
        if not isinstance(row, dict) or row.keys() - allowed:
            raise ValidationError("Unknown proposal fields.")
        result = {"artifact": {}, "request": {}, "parent_id": None, **row}
        _text(result.get("id"), "proposal id")
        _text(result.get("title"), "title", 300)
        if result.get("kind") not in ("parameter", "rule"):
            raise ValidationError("Invalid proposal kind.")
        if result.get("status") not in (
            "evaluating", "ready", "no_change", "blocked", "investigation_requested",
            "investigation_approved", "needs_engine_support",
        ):
            raise ValidationError("Invalid initial proposal status.")
        if not isinstance(result["artifact"], dict) or not isinstance(result["request"], dict):
            raise ValidationError("Artifact and request must be objects.")
        digest = artifact_digest(result["artifact"])
        if result.get("artifact_sha256", digest) != digest:
            raise ValidationError("Artifact fingerprint mismatch.")
        result["artifact_sha256"] = digest
        artifact_digest(result)
        return result

    def create_proposal(self, row):
        return self._rpc("create", {"row": self._new_proposal(row)})

    def update_proposal(self, identifier, expected_revision, patch, event,
                        note="", data=None, child=None):
        if not isinstance(patch, dict) or patch.keys() - {
            "status", "artifact", "artifact_sha256", "request", "approval",
        }:
            raise ValidationError("Unknown proposal update fields.")
        patch = dict(patch)
        if "artifact" in patch:
            if not isinstance(patch["artifact"], dict):
                raise ValidationError("Artifact must be an object.")
            digest = artifact_digest(patch["artifact"])
            if patch.get("artifact_sha256", digest) != digest:
                raise ValidationError("Artifact fingerprint mismatch.")
            patch["artifact_sha256"] = digest
        elif "artifact_sha256" in patch:
            raise ValidationError("Cannot update the fingerprint without its artifact.")
        if not isinstance(note, str) or len(note) > 4000:
            raise ValidationError("Note must be at most 4000 characters.")
        payload = {"id": _text(identifier, "proposal id"),
                   "expected_revision": _revision(expected_revision), "patch": patch,
                   "event": _text(event, "event"), "note": note, "data": data or {},
                   "event_id": uuid.uuid4().hex}
        if child is not None:
            payload["child"] = self._new_proposal(child)
        artifact_digest(payload)
        return self._rpc("update", payload)

    def add_event(self, proposal_id, event, note="", data=None, event_id=None):
        if not isinstance(note, str) or len(note) > 4000:
            raise ValidationError("Note must be at most 4000 characters.")
        payload = {"id": event_id or uuid.uuid4().hex, "proposal_id": proposal_id,
                   "event": _text(event, "event"), "note": note, "data": data or {}}
        artifact_digest(payload)
        return self._rpc("event", payload)

    def refresh_risk_analytics(self, identifier, expected_revision, artifact, risk_analytics):
        if not isinstance(artifact, dict) or not isinstance(risk_analytics, dict):
            raise ValidationError("Artifact and risk analytics must be objects.")
        payload = {
            "id": _text(identifier, "proposal id"),
            "expected_revision": _revision(expected_revision),
            "expected_artifact_sha256": artifact_digest(artifact),
            "risk_analytics": risk_analytics,
            "artifact_sha256": artifact_digest({**artifact, "risk_analytics": risk_analytics}),
            "event_id": uuid.uuid4().hex,
        }
        artifact_digest(payload)
        try:
            result = self._query(self.client.rpc("intraday_calibration_refresh_risk", {
                "p_payload": payload,
            }))
        except StoreUnavailable as exc:
            if getattr(exc.__cause__, "code", None) in ("PGRST202", "42883"):
                raise StoreUnavailable(
                    "Diagnostic refresh RPC is missing; apply "
                    "migrations/20261003_refresh_calibration_risk_diagnostics.sql."
                ) from exc
            raise
        if result is None:
            raise StoreUnavailable("Research inbox write was not confirmed.")
        return result

    def health(self):
        rows = self._query(self.client.table("intraday_calibration_health").select("*")
                           .eq("id", "calibration-worker").limit(1))
        return rows[0] if rows else None

    def set_health(self, status, error=None, metrics=None):
        return self._rpc("health", {
            "status": _text(status, "health status", 80),
            "last_error": str(error)[:500] if error else None, "metrics": metrics or {},
        })

    def claim(self, owner, seconds=600):
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise ValidationError("Lease duration must be between 1 and 3600 seconds.")
        return self._rpc("claim", {"owner": _text(owner, "lease owner"), "seconds": seconds}) is True

    def release(self, owner):
        return self._rpc("release", {"owner": _text(owner, "lease owner")})

    def mark_notified(self, event_id):
        return self._rpc("notified", {"id": _text(event_id, "event id")})
