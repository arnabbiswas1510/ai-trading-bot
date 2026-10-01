"""Operator-authenticated live-entry control; no broker or research writes."""
import hmac
import os

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, StrictBool

import trading_control

router = APIRouter()


class EntryPermission(BaseModel):
    live_entries_enabled: StrictBool


def _configured_token():
    token = os.getenv("TRADING_CONTROL_TOKEN", "")
    return token if len(token) >= 32 else ""


def _status():
    result = trading_control.get_status()
    return {**result, "write_configured": bool(_configured_token())}


@router.get("/api/trading-control")
def get_trading_control(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return _status()


@router.put("/api/trading-control")
def update_trading_control(
    permission: EntryPermission,
    response: Response,
    authorization: str = Header(default=""),
):
    response.headers["Cache-Control"] = "no-store"
    token = _configured_token()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Dashboard trading control is locked. Configure a private "
                   "TRADING_CONTROL_TOKEN of at least 32 characters on the server.",
        )
    if not hmac.compare_digest(
        authorization.encode("utf-8"), f"Bearer {token}".encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid trading-control token.")
    try:
        trading_control.set_entries_enabled(permission.live_entries_enabled)
    except trading_control.ControlUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Trading permission could not be saved. Do not assume it changed.",
        ) from exc
    return _status()
