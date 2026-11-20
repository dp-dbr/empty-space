"""ValidationRules for the 'application' SubjectType (extension point 6)."""

from __future__ import annotations

import re

from dp_agentic_collector.extensions.interfaces import (
    RuleViolation,
    ValidationContext,
    ValidationRule,
)


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


class HighCriticalityNeedsTechnicalOwner(ValidationRule):
    name = "high_criticality_requires_technical_owner"

    def check(self, data, ctx: ValidationContext) -> list[RuleViolation]:
        if data.get("criticality") == "high" and not data.get("technical_owner"):
            return [RuleViolation(self.name, "technical_owner",
                                  "Applications with criticality 'high' require a technical owner.")]
        return []


class InterfacesReferenceExistingApplications(ValidationRule):
    name = "interfaces_reference_existing_applications"

    def check(self, data, ctx: ValidationContext) -> list[RuleViolation]:
        known = set()
        for key, peer in ctx.peer_data.items():
            known.add(_key(key))
            if isinstance(peer, dict) and peer.get("name"):
                known.add(_key(str(peer["name"])))
        out = []
        for i, ref in enumerate(data.get("interfaces") or []):
            if _key(str(ref)) not in known:
                out.append(RuleViolation(self.name, f"interfaces[{i}]",
                                         f"'{ref}' is not a known application in this cycle."))
        return out


RULES = [HighCriticalityNeedsTechnicalOwner, InterfacesReferenceExistingApplications]
