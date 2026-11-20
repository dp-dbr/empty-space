"""CredentialVault: Fernet-encrypted secrets in the database (ADR-0007)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import and_, delete, insert, select, update

from ..domain.errors import NotFound
from ..domain.ids import new_id, utcnow
from ..extensions.interfaces import CredentialVault
from .sql.db import Database
from .sql.schema import credential_secrets as S

log = logging.getLogger(__name__)


def load_keys(configured: str, data_dir: Path) -> MultiFernet:
    keys = [k.strip() for k in configured.split(",") if k.strip()]
    if not keys:
        key_file = Path(data_dir) / "vault.key"
        if not key_file.exists():
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(Fernet.generate_key().decode())
            key_file.chmod(0o600)
            log.warning("DP_AC_VAULT_KEYS not set; generated a local dev key at %s", key_file)
        keys = [key_file.read_text().strip()]
    return MultiFernet([Fernet(k.encode()) for k in keys])


class DbCredentialVault(CredentialVault):
    def __init__(self, db: Database, fernet: MultiFernet) -> None:
        self.db = db
        self.fernet = fernet

    def _enc(self, secret: Mapping[str, Any]) -> str:
        return self.fernet.encrypt(json.dumps(dict(secret)).encode()).decode()

    def put(self, tenant_id: str, owner_id: str, secret: Mapping[str, Any]) -> str:
        ref = new_id("cred")
        now = utcnow()
        with self.db.engine.begin() as c:
            c.execute(insert(S).values(ref=ref, tenant_id=tenant_id, owner_id=owner_id,
                                       ciphertext=self._enc(secret), created_at=now, updated_at=now))
        return ref

    def get(self, tenant_id: str, ref: str) -> dict[str, Any]:
        with self.db.engine.connect() as c:
            row = c.execute(select(S).where(and_(S.c.ref == ref, S.c.tenant_id == tenant_id))).first()
        if row is None:
            raise NotFound("credential not found")
        try:
            return json.loads(self.fernet.decrypt(row.ciphertext.encode()))  # type: ignore[no-any-return]
        except InvalidToken:
            raise NotFound("credential cannot be decrypted") from None

    def replace(self, tenant_id: str, ref: str, secret: Mapping[str, Any]) -> None:
        with self.db.engine.begin() as c:
            res = c.execute(update(S).where(and_(S.c.ref == ref, S.c.tenant_id == tenant_id))
                            .values(ciphertext=self._enc(secret), updated_at=utcnow()))
        if res.rowcount == 0:
            raise NotFound("credential not found")

    def delete(self, tenant_id: str, ref: str) -> None:
        with self.db.engine.begin() as c:
            c.execute(delete(S).where(and_(S.c.ref == ref, S.c.tenant_id == tenant_id)))

    def rotate(self) -> int:
        """Re-encrypt all secrets with the primary key."""
        with self.db.engine.begin() as c:
            rows = c.execute(select(S.c.ref, S.c.ciphertext)).fetchall()
            for r in rows:
                c.execute(update(S).where(S.c.ref == r.ref)
                          .values(ciphertext=self.fernet.rotate(r.ciphertext.encode()).decode()))
        return len(rows)
