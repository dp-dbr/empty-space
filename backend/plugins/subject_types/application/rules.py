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


def interface_symmetry(latest):
    """Contradiction: A declares an interface to B, but B does not declare one to A."""
    from dp_agentic_collector.extensions.interfaces import ConsolidationIssue

    names = {_key(k): k for k in latest}
    for k, d in latest.items():
        if d.get("name"):
            names[_key(str(d["name"]))] = k
    issues = []
    for key, data in latest.items():
        for ref in data.get("interfaces") or []:
            other = names.get(_key(str(ref)))
            if other is None or other == key:
                continue
            back = {names.get(_key(str(x))) for x in latest[other].get("interfaces") or []}
            if key not in back:
                issues.append(ConsolidationIssue("contradiction", "interface_symmetry", (key, other), "interfaces",
                                                 f"{key} declares an interface to {other}, but {other} does not "
                                                 f"declare one to {key}."))
    return issues


def owner_conflicts(latest):
    """Contradiction: the same application name is described by several subjects with different owners."""
    from dp_agentic_collector.extensions.interfaces import ConsolidationIssue

    by_name: dict[str, list[str]] = {}
    for key, data in latest.items():
        if data.get("name"):
            by_name.setdefault(_key(str(data["name"])), []).append(key)
    issues = []
    for name, keys in by_name.items():
        owners = {str(latest[k].get("business_owner")) for k in keys}
        if len(keys) > 1 and len(owners) > 1:
            issues.append(ConsolidationIssue("contradiction", "owner_conflict", tuple(keys), "business_owner",
                                             f"'{name}' has different business owners: {sorted(owners)}"))
    return issues


CONSOLIDATION_CHECKS = [interface_symmetry, owner_conflicts]
