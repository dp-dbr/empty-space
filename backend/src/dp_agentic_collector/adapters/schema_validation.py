"""JSON Schema validation (stage 1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator

from ..extensions.interfaces import RuleViolation


def validate_schema(schema: Mapping[str, Any], data: Mapping[str, Any]) -> list[RuleViolation]:
    validator = Draft202012Validator(schema)
    out = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "$"
        out.append(RuleViolation(rule="schema", path=path, message=err.message))
    return out
