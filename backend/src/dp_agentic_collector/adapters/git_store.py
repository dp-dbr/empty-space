"""Git-based ArtifactStore (pygit2). Layout is an implementation detail (ADR-0005)."""

from __future__ import annotations

import fcntl
import json
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pygit2

from ..domain.artifacts import ArtifactDiff, ArtifactVersion, NewArtifact, Snapshot, VersionSelector
from ..domain.errors import NotFound, ValidationFailed, VersionConflict
from ..domain.ids import utcnow
from ..domain.state_machines import check_artifact_transition
from ..extensions.interfaces import ArtifactStore
from .diff import diff_versions

MAIN = "refs/heads/main"
VERSION_REF = "refs/dpac/versions/{sid}/{n:08d}"
LATEST_REF = "refs/dpac/latest/{sid}"
SNAPSHOT_REF = "refs/tags/snapshots/{cycle}/{name}"


class GitArtifactStore(ArtifactStore):
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # --- infrastructure -------------------------------------------------------------
    def _repo(self, tenant_id: str) -> pygit2.Repository:
        if "/" in tenant_id or ".." in tenant_id:
            raise ValidationFailed("invalid tenant id")
        path = self.root / f"{tenant_id}.git"
        if not path.exists():
            return pygit2.init_repository(str(path), bare=True)
        return pygit2.Repository(str(path))

    @contextmanager
    def _locked(self, tenant_id: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(tenant_id, threading.Lock())
        with lock:
            lock_path = self.root / f"{tenant_id}.lock"
            with open(lock_path, "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)

    @staticmethod
    def _sig(name: str, when: datetime) -> pygit2.Signature:
        return pygit2.Signature(name, f"{name}@dpac.invalid", int(when.timestamp()), 0)

    @staticmethod
    def _upsert(repo: pygit2.Repository, tree: pygit2.Tree | None, parts: list[str], blob_id: pygit2.Oid) -> pygit2.Oid:
        tb = repo.TreeBuilder(tree) if tree is not None else repo.TreeBuilder()
        head, rest = parts[0], parts[1:]
        if not rest:
            tb.insert(head, blob_id, pygit2.GIT_FILEMODE_BLOB)
        else:
            sub = None
            if tree is not None and head in tree:
                sub = repo[tree[head].id]
            tb.insert(head, GitArtifactStore._upsert(repo, sub, rest, blob_id), pygit2.GIT_FILEMODE_TREE)
        return tb.write()

    @staticmethod
    def _read(repo: pygit2.Repository, commit: pygit2.Commit, path: str) -> str:
        try:
            entry = commit.tree[path]
        except KeyError:
            raise NotFound(f"{path} not found in commit") from None
        return repo[entry.id].data.decode("utf-8")

    def _head(self, repo: pygit2.Repository) -> pygit2.Commit | None:
        try:
            return repo.references[MAIN].peel(pygit2.Commit)
        except KeyError:
            return None

    def _version_commit(self, repo: pygit2.Repository, subject_id: str, version: int) -> pygit2.Commit:
        try:
            return repo.references[VERSION_REF.format(sid=subject_id, n=version)].peel(pygit2.Commit)
        except KeyError:
            raise NotFound(f"version {version} of {subject_id} not found") from None

    def _load(self, repo: pygit2.Repository, commit: pygit2.Commit, subject_id: str) -> ArtifactVersion:
        base = f"subjects/{subject_id}"
        return ArtifactVersion.from_files(self._read(repo, commit, f"{base}/data.json"),
                                          self._read(repo, commit, f"{base}/factsheet.md"))

    # --- ArtifactStore --------------------------------------------------------------
    def latest_version(self, tenant_id: str, subject_id: str) -> int | None:
        repo = self._repo(tenant_id)
        try:
            ref = repo.references[LATEST_REF.format(sid=subject_id)]
        except KeyError:
            return None
        commit = ref.peel(pygit2.Commit)
        return self._load(repo, commit, subject_id).header.version

    def put(self, new: NewArtifact) -> ArtifactVersion:
        with self._locked(new.tenant_id):
            repo = self._repo(new.tenant_id)
            latest = self.latest_version(new.tenant_id, new.subject_id)
            if new.based_on != latest:
                raise VersionConflict(
                    f"based_on v{new.based_on} is outdated; latest is v{latest}",
                    subject_id=new.subject_id, based_on=new.based_on, latest=latest,
                )
            prev_status = None
            if latest is not None:
                prev_status = self.get(new.tenant_id, new.subject_id, VersionSelector(latest)).header.status
            check_artifact_transition(prev_status, new.status)
            created = utcnow()
            version = new.build((latest or 0) + 1, created)
            head = self._head(repo)
            tree = head.tree if head else None
            base = ["subjects", new.subject_id]
            tree_id = self._upsert(repo, tree, base + ["data.json"], repo.create_blob(version.data_file().encode()))
            tree_id = self._upsert(repo, repo[tree_id], base + ["factsheet.md"],
                                   repo.create_blob(version.factsheet_file().encode()))
            sig = self._sig(new.contributor.user_id, created)
            h = version.header
            message = (f"{h.subject_id} v{h.version} [{h.status}] {h.change_reason}\n\n"
                       + json.dumps(h.to_dict(), indent=2))
            oid = repo.create_commit(MAIN, sig, sig, message, tree_id, [head.id] if head else [])
            repo.references.create(VERSION_REF.format(sid=new.subject_id, n=h.version), oid)
            repo.references.create(LATEST_REF.format(sid=new.subject_id), oid, force=True)
            return version

    def get(self, tenant_id: str, subject_id: str, selector: VersionSelector | None = None) -> ArtifactVersion:
        selector = selector or VersionSelector.latest()
        repo = self._repo(tenant_id)
        if selector.snapshot:
            cycle, _, name = selector.snapshot.partition("/")
            snap = self.get_snapshot(tenant_id, cycle, name)
            if subject_id not in snap.versions:
                raise NotFound(f"{subject_id} not in snapshot {selector.snapshot}")
            version = snap.versions[subject_id]
        elif selector.version is not None:
            version = selector.version
        else:
            latest = self.latest_version(tenant_id, subject_id)
            if latest is None:
                raise NotFound(f"no versions for {subject_id}")
            version = latest
        return self._load(repo, self._version_commit(repo, subject_id, version), subject_id)

    def history(self, tenant_id: str, subject_id: str) -> list[ArtifactVersion]:
        latest = self.latest_version(tenant_id, subject_id)
        if latest is None:
            return []
        return [self.get(tenant_id, subject_id, VersionSelector(v)) for v in range(1, latest + 1)]

    def diff(self, a: ArtifactVersion, b: ArtifactVersion) -> ArtifactDiff:
        return diff_versions(a, b)

    def _subjects(self, repo: pygit2.Repository) -> list[str]:
        head = self._head(repo)
        if head is None or "subjects" not in head.tree:
            return []
        return [e.name for e in repo[head.tree["subjects"].id]]

    def as_of(self, tenant_id: str, cycle_id: str, at: datetime) -> dict[str, ArtifactVersion]:
        repo = self._repo(tenant_id)
        out: dict[str, ArtifactVersion] = {}
        for sid in self._subjects(repo):
            for v in reversed(self.history(tenant_id, sid)):
                if v.header.cycle == cycle_id and v.header.created <= at:
                    out[sid] = v
                    break
        return out

    def verify(self, tenant_id: str, subject_id: str, version: int) -> bool:
        try:
            return self.get(tenant_id, subject_id, VersionSelector(version)).verify()
        except ValidationFailed:
            return False

    def snapshot(self, tenant_id: str, cycle_id: str, name: str, created_by: str,
                 subject_ids: Iterable[str]) -> Snapshot:
        if "/" in name or not name:
            raise ValidationFailed("invalid snapshot name")
        with self._locked(tenant_id):
            repo = self._repo(tenant_id)
            ref = SNAPSHOT_REF.format(cycle=cycle_id, name=name)
            if ref in repo.references:
                raise ValidationFailed(f"snapshot {name} already exists")
            versions = {}
            for sid in subject_ids:
                latest = self.latest_version(tenant_id, sid)
                if latest is not None:
                    versions[sid] = latest
            snap = Snapshot(tenant_id=tenant_id, cycle_id=cycle_id, name=name, created=utcnow(),
                            created_by=created_by, versions=versions)
            manifest = json.dumps({"tenant_id": tenant_id, "cycle_id": cycle_id, "name": name,
                                   "created": snap.created.isoformat(), "created_by": created_by,
                                   "versions": versions}, indent=2, sort_keys=True)
            head = self._head(repo)
            tree_id = self._upsert(repo, head.tree if head else None, ["snapshot.json"],
                                   repo.create_blob(manifest.encode()))
            sig = self._sig(created_by, snap.created)
            oid = repo.create_commit(None, sig, sig, f"snapshot {cycle_id}/{name}", tree_id,
                                     [head.id] if head else [])
            repo.references.create(ref, oid)
            return snap

    def get_snapshot(self, tenant_id: str, cycle_id: str, name: str) -> Snapshot:
        repo = self._repo(tenant_id)
        try:
            commit = repo.references[SNAPSHOT_REF.format(cycle=cycle_id, name=name)].peel(pygit2.Commit)
        except KeyError:
            raise NotFound(f"snapshot {cycle_id}/{name} not found") from None
        m = json.loads(self._read(repo, commit, "snapshot.json"))
        return Snapshot(tenant_id=m["tenant_id"], cycle_id=m["cycle_id"], name=m["name"],
                        created=datetime.fromisoformat(m["created"]), created_by=m["created_by"],
                        versions={k: int(v) for k, v in m["versions"].items()})

    def list_snapshots(self, tenant_id: str, cycle_id: str) -> list[Snapshot]:
        repo = self._repo(tenant_id)
        prefix = f"refs/tags/snapshots/{cycle_id}/"
        names = [r[len(prefix):] for r in repo.references if r.startswith(prefix)]
        return sorted((self.get_snapshot(tenant_id, cycle_id, n) for n in names), key=lambda s: s.created)
