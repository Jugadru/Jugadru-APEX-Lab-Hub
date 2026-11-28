"""Versioned, deny-by-default external AI provider interface; no model runtime."""
from dataclasses import dataclass

SCHEMA = 1
GOALS = frozenset(("review_evidence", "review_plan", "suggest_next_steps"))

@dataclass(frozen=True)
class Provider:
    id: str
    transport: str
    model: str
    enabled: bool
    activation: str

    def describe(self):
        return {"id": self.id, "transport": self.transport, "model": self.model,
                "enabled": self.enabled, "activation": self.activation,
                "goals": sorted(GOALS) if self.enabled else [],
                "identity_verified": False, "execution_authorized": False,
                "hub_network_calls": 0, "hub_paid_model_calls": 0,
                "host_cost_enforced": False, "response_authority": "advisory_only"}

_PROFILES = (
    Provider("mcp_stdio", "local stdio", "host-selected; unverified", True, "reviewed local context transport"),
    Provider("manual_ai", "operator-mediated local export", "operator-selected; unverified", True, "reviewed operator exchange"),
    Provider("chatgpt", "not configured", "unverified", False, "requires reviewed ChatGPT bridge"),
    Provider("codex", "not configured", "unverified", False, "requires reviewed Codex bridge"),
    Provider("local_ai", "not configured", "unverified", False, "requires reviewed local runtime bridge"),
    Provider("remote_mcp", "not configured", "unverified", False, "requires separate authorization"),
    Provider("openai_api", "not configured", "unverified", False, "requires separate authorization"),
    Provider("other_api", "not configured", "unverified", False, "requires separate authorization"),
)

def resolve(provider, goal):
    """Return a reviewed context transport, not an executable agent."""
    if type(provider) is not str or type(goal) is not str or goal not in GOALS:
        raise ValueError("Unsupported provider or goal")
    for profile in _PROFILES:
        if profile.id == provider and profile.enabled:
            return profile
    raise ValueError("Provider unavailable or disabled")

def active_transports():
    """Preserve existing schema-1 Gateway transport catalog."""
    return {p.id: {"transport": p.transport, "model": p.model}
            for p in _PROFILES if p.enabled}

def catalog():
    """Return a fresh offline inventory; never imply a connection or live model."""
    return {"schema": SCHEMA, "providers": [p.describe() for p in _PROFILES],
            "default_provider": None, "external_agents_connected": False,
            "approval_required": True, "automatic_fallback": False}
