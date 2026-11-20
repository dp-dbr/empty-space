"""SubjectType deployable as a folder package (``subject_type.yaml`` + schema + templates +
rules + guide). Templates are rendered in a Jinja2 sandbox."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from ..domain.errors import ValidationFailed
from ..extensions.interfaces import SchemaMigration, SubjectType, ValidationRule


class FolderSubjectType(SubjectType):
    def __init__(self, folder: Path, manifest: dict[str, Any]) -> None:
        self.folder = folder
        self.manifest = manifest
        self.name = manifest["name"]
        self.schema_version = str(manifest.get("schema_version", "1"))
        self.title = manifest.get("title", {"en": self.name})
        self._schema = json.loads((folder / manifest.get("schema", "schema.json")).read_text())
        self._env = SandboxedEnvironment(autoescape=False, undefined=StrictUndefined, trim_blocks=True,
                                         lstrip_blocks=True, keep_trailing_newline=True)
        self._env.filters["tojson"] = lambda v: json.dumps(v, ensure_ascii=False)
        self._rules: list[ValidationRule] = []
        self._migrations: list[SchemaMigration] = []
        if manifest.get("rules"):
            module = self._import(folder / manifest["rules"], "rules")
            self._rules = [r() if isinstance(r, type) else r for r in getattr(module, "RULES", [])]
        if manifest.get("migrations"):
            module = self._import(folder / manifest["migrations"], "migrations")
            self._migrations = list(getattr(module, "MIGRATIONS", []))

    def _import(self, path: Path, suffix: str) -> Any:
        mod_name = f"dpac_subject_type_{self.name}_{suffix}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module

    @classmethod
    def load(cls, folder: Path | str) -> FolderSubjectType:
        folder = Path(folder)
        manifest = yaml.safe_load((folder / "subject_type.yaml").read_text())
        if not isinstance(manifest, dict) or "name" not in manifest:
            raise ValidationFailed(f"invalid subject_type.yaml in {folder}")
        return cls(folder, manifest)

    def json_schema(self) -> dict[str, Any]:
        return self._schema

    def supported_locales(self) -> Sequence[str]:
        return tuple(self.manifest.get("templates", {"en": None}).keys())

    def _localized_file(self, key: str, locale: str) -> Path:
        files: dict[str, str] = self.manifest.get(key, {})
        name = files.get(locale) or files.get("en") or next(iter(files.values()))
        return self.folder / name

    def render_factsheet(self, data: Mapping[str, Any], context: Mapping[str, Any], locale: str) -> str:
        template = self._env.from_string(self._localized_file("templates", locale).read_text())
        return template.render(data=data, ctx=context, locale=locale)

    def rules(self) -> Sequence[ValidationRule]:
        return self._rules

    def conversation_guide(self, locale: str) -> str:
        return self._localized_file("guides", locale).read_text()

    def migrations(self) -> Sequence[SchemaMigration]:
        return self._migrations
