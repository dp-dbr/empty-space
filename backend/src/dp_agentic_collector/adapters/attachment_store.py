"""Content-addressed AttachmentStore on the local filesystem (S3-compatible adapter later)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..domain.errors import NotFound, ValidationFailed
from ..extensions.interfaces import AttachmentStore


class FilesystemAttachmentStore(AttachmentStore):
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, tenant_id: str, content_hash: str) -> Path:
        algo, _, digest = content_hash.partition(":")
        if algo != "sha256" or len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise ValidationFailed("invalid content hash")
        if "/" in tenant_id or ".." in tenant_id:
            raise ValidationFailed("invalid tenant id")
        return self.root / tenant_id / digest[:2] / digest

    def put(self, tenant_id: str, content: bytes) -> str:
        content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        path = self._path(tenant_id, content_hash)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(content)
            os.replace(tmp, path)
        return content_hash

    def get(self, tenant_id: str, content_hash: str) -> bytes:
        path = self._path(tenant_id, content_hash)
        if not path.exists():
            raise NotFound("attachment content not found")
        return path.read_bytes()

    def exists(self, tenant_id: str, content_hash: str) -> bool:
        return self._path(tenant_id, content_hash).exists()

    def erase(self, tenant_id: str, content_hash: str) -> None:
        path = self._path(tenant_id, content_hash)
        if path.exists():
            path.unlink()
