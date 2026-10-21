# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.2,<2", "httpx>=0.27"]
# ///
"""Zenduty MCP server: read tools plus confirm-gated acknowledge/resolve/note.

Endpoints mirror the official zenduty-python-sdk. Incident listing uses
POST /api/incidents/filter/ because GET /api/incidents/ answers 209 "Blocked".
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("ZENDUTY_BASE_URL", "https://www.zenduty.com").rstrip("/")
SECRETS_FILE = Path.home() / ".secrets" / "zenduty_env"
STATUS_CODE = {"triggered": 1, "acknowledged": 2, "resolved": 3}
STATUS_NAME = {v: k for k, v in STATUS_CODE.items()}


def _load_key() -> str:
    key = os.environ.get("ZENDUTY_API_KEY", "").strip()
    if key:
        return key
    if SECRETS_FILE.is_file():
        match = re.search(r'ZENDUTY_API_KEY="?([^"\s]+)"?', SECRETS_FILE.read_text())
        if match:
            return match.group(1)
    raise RuntimeError(f"ZENDUTY_API_KEY not set and not found in {SECRETS_FILE}")


_http = httpx.Client(
    base_url=BASE_URL,
    headers={"Authorization": f"Token {_load_key()}", "Content-Type": "application/json"},
    timeout=30.0,
)
mcp = FastMCP("zenduty")


SECRET_FIELDS = frozenset({"integration_key", "webhook_url"})


def _redact(value: Any) -> Any:
    """Mask ingestion secrets (integration keys, webhooks) nested anywhere in API payloads."""
    if isinstance(value, dict):
        return {k: "[redacted]" if k in SECRET_FIELDS else _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _call(method: str, path: str, **kwargs: Any) -> Any:
    resp = _http.request(method, path, **kwargs)
    if resp.status_code >= 300:
        raise RuntimeError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
    return _redact(resp.json()) if resp.content else None


def _incident_summary(inc: dict[str, Any]) -> dict[str, Any]:
    return {
        "incident_number": inc.get("incident_number"),
        "title": inc.get("title"),
        "status": STATUS_NAME.get(inc.get("status"), inc.get("status")),
        "priority": (inc.get("team_priority_object") or {}).get("name"),
        "service": (inc.get("service_object") or {}).get("name"),
        "assigned_to": inc.get("assigned_to_name"),
        "created": inc.get("creation_date"),
        "acknowledged": inc.get("acknowledged_date"),
        "resolved": inc.get("resolved_date"),
    }


def _require_confirm(confirm: bool, action: str) -> None:
    if not confirm:
        raise ValueError(f"Refusing to {action}: pass confirm=true after the user approves this exact action.")


@mcp.tool()
def list_teams() -> list[dict[str, Any]]:
    """List Zenduty teams (name + unique_id). Team ids scope services, on-call, schedules, policies."""
    return [{"name": t["name"], "unique_id": t["unique_id"]} for t in _call("GET", "/api/account/teams/")]


@mcp.tool()
def list_services(team_id: str) -> list[dict[str, Any]]:
    """List services in a team."""
    services = _call("GET", f"/api/account/teams/{team_id}/services/")
    return [{"name": s.get("name"), "unique_id": s.get("unique_id")} for s in services]


@mcp.tool()
def list_incidents(
    status: Literal["triggered", "acknowledged", "resolved", "open"] = "open",
    team_ids: list[str] | None = None,
    service_ids: list[str] | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> list[dict[str, Any]]:
    """List incidents, newest first. status "open" = triggered + acknowledged. Dates as YYYY-MM-DD."""
    statuses = ["triggered", "acknowledged"] if status == "open" else [status]
    results: list[dict[str, Any]] = []
    for name in statuses:
        payload: dict[str, Any] = {
            "status": STATUS_CODE[name],
            "all_teams": 0 if team_ids else 1,
            "postmortem_filter": -1,
            "page": page,
            "page_size": page_size,
        }
        optional = {"team_ids": team_ids, "service_ids": service_ids, "from_date": from_date, "to_date": to_date}
        payload.update({k: v for k, v in optional.items() if v})
        data = _call("POST", f"/api/incidents/filter/?page={page}&page_size={page_size}", json=payload)
        results.extend(_incident_summary(i) for i in data.get("results", []))
    return sorted(results, key=lambda i: i["created"] or "", reverse=True)


@mcp.tool()
def get_incident(incident_number: int) -> dict[str, Any]:
    """Full incident record by incident number."""
    return _call("GET", f"/api/incidents/{incident_number}/")


@mcp.tool()
def get_incident_alerts(incident_number: int) -> list[dict[str, Any]]:
    """Alerts/events attached to an incident."""
    return _call("GET", f"/api/incidents/{incident_number}/alerts/").get("results", [])


@mcp.tool()
def get_incident_notes(incident_number: int) -> list[dict[str, Any]]:
    """Notes on an incident."""
    return _call("GET", f"/api/incidents/{incident_number}/note/").get("results", [])


@mcp.tool()
def get_oncall(team_id: str | None = None) -> list[dict[str, Any]]:
    """Who is on call now, per escalation policy. Omit team_id for all teams."""
    team_ids = [team_id] if team_id else [t["unique_id"] for t in list_teams()]
    return [
        {"team_id": tid, "oncall": _call("GET", f"/api/account/teams/{tid}/oncall/")}
        for tid in team_ids
    ]


@mcp.tool()
def list_schedules(team_id: str) -> list[dict[str, Any]]:
    """On-call schedules in a team."""
    return _call("GET", f"/api/account/teams/{team_id}/schedules/")


@mcp.tool()
def list_escalation_policies(team_id: str) -> list[dict[str, Any]]:
    """Escalation policies in a team."""
    return _call("GET", f"/api/account/teams/{team_id}/escalation_policies/")


def _set_status(incident_number: int, status: int) -> dict[str, Any]:
    current = _call("GET", f"/api/incidents/{incident_number}/")
    service = current.get("service") or (current.get("service_object") or {}).get("unique_id")
    payload = {"title": current["title"], "summary": current.get("summary"), "status": status, "service": service}
    return _incident_summary(_call("PUT", f"/api/incidents/{incident_number}/", json=payload))


@mcp.tool()
def acknowledge_incident(incident_number: int, confirm: bool = False) -> dict[str, Any]:
    """WRITE: acknowledge an incident. Requires confirm=true after explicit user approval."""
    _require_confirm(confirm, f"acknowledge incident {incident_number}")
    return _set_status(incident_number, STATUS_CODE["acknowledged"])


@mcp.tool()
def resolve_incident(incident_number: int, confirm: bool = False) -> dict[str, Any]:
    """WRITE: resolve an incident. Requires confirm=true after explicit user approval."""
    _require_confirm(confirm, f"resolve incident {incident_number}")
    return _set_status(incident_number, STATUS_CODE["resolved"])


@mcp.tool()
def add_incident_note(incident_number: int, note: str, confirm: bool = False) -> dict[str, Any]:
    """WRITE: add a note to an incident. Requires confirm=true after explicit user approval."""
    _require_confirm(confirm, f"add a note to incident {incident_number}")
    return _call("POST", f"/api/incidents/{incident_number}/note/", json={"note": note})


if __name__ == "__main__":
    mcp.run()
