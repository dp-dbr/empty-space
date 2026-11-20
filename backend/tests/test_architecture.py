"""Guardrail: the domain package imports only the standard library and itself."""

import ast
import sys
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[1] / "src" / "dp_agentic_collector" / "domain"
EXTENSIONS = DOMAIN.parent / "extensions"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def test_domain_imports_only_stdlib():
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    for f in DOMAIN.rglob("*.py"):
        bad = _imports(f) - stdlib
        assert not bad, f"{f.name} imports third-party modules {bad}"


def test_extension_interfaces_import_no_third_party():
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    for f in EXTENSIONS.rglob("*.py"):
        bad = _imports(f) - stdlib
        assert not bad, f"{f.name} imports third-party modules {bad}"
