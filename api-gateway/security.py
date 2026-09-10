"""Centralized action-risk policy for Jarvis privileged tools.

The execution router must never contain a tool that is absent from this policy.
Unknown tools fail closed.  Risk classes separate read-only inspection,
low-impact local/UI operations, internal Jarvis writes, and sensitive actions
that require an explicit confirmation ticket.
"""
from __future__ import annotations

READ_ONLY = {
    "google_accounts", "file_search", "file_content_search", "read_file",
    "gmail_search", "gmail_read", "calendar_list", "home_states", "home_entities",
    "local_tools_inventory", "connections_inventory", "research_search",
    "computer_status", "computer_capabilities", "computer_verify", "computer_observe",
    "computer_screenshot", "computer_processes", "computer_windows",
}

# These can change local UI/session state but are intentionally non-destructive.
# They remain auditable and are only reachable through an explicit tool call.
LOW_IMPACT = {
    "open_file", "computer_focus", "computer_move", "computer_scroll",
}

# Writes only to Jarvis-owned internal state/artifacts; never external services.
INTERNAL_WRITE = {
    "artifact_create",
}

SENSITIVE = {
    "write_file", "gmail_send", "calendar_create", "calendar_update", "calendar_delete",
    "home_device", "computer_open", "computer_click", "computer_type", "computer_key",
    "computer_shell",
}

KNOWN_TOOLS = READ_ONLY | LOW_IMPACT | INTERNAL_WRITE | SENSITIVE


def risk(tool: str) -> str:
    if tool in READ_ONLY:
        return "read"
    if tool in LOW_IMPACT:
        return "low_impact"
    if tool in INTERNAL_WRITE:
        return "internal_write"
    if tool in SENSITIVE:
        return "write"
    return "unknown"


def allowed(tool: str) -> bool:
    return tool in KNOWN_TOOLS


def requires_confirmation(tool: str) -> bool:
    return risk(tool) == "write"


def policy_snapshot() -> dict:
    return {
        "read_only": sorted(READ_ONLY),
        "low_impact": sorted(LOW_IMPACT),
        "internal_write": sorted(INTERNAL_WRITE),
        "sensitive": sorted(SENSITIVE),
        "unknown_default": "deny",
    }
