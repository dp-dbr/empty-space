"""Contract tests for ArtifactStore implementations (run against every implementation)."""

from datetime import timedelta

import pytest

from dp_agentic_collector.adapters.git_store import GitArtifactStore
from dp_agentic_collector.domain.artifacts import (
    ArtifactVersion,
    Contributor,
    NewArtifact,
    SourceRef,
    VersionSelector,
)
from dp_agentic_collector.domain.enums import ArtifactStatus
from dp_agentic_collector.domain.errors import InvalidTransition, NotFound, VersionConflict
from dp_agentic_collector.domain.ids import utcnow

T = "ten_a"


@pytest.fixture(params=["git"])
def store(request, tmp_path):
    if request.param == "git":
        return GitArtifactStore(tmp_path / "artifacts")
    raise AssertionError


def new(subject="sub_1", based_on=None, status=ArtifactStatus.DRAFT, data=None, body="# Fact\n", cycle="cyc_1",
        reason="initial", sources=()):
    return NewArtifact(tenant_id=T, subject_id=subject, cycle=cycle, based_on=based_on, schema_version="1",
                       locale="en", status=status, contributor=Contributor("usr_1", "run_1"),
                       change_reason=reason, data=data or {"name": "CRM"}, factsheet_body=body,
                       sources=tuple(sources))


def test_put_get_latest_and_header_pair(store):
    src = SourceRef(kind="mcp", on_behalf_of="usr_1", retrieved_at="2026-01-01T00:00:00Z", server="s", tool="t",
                    fields=("name",))
    v1 = store.put(new(sources=[src]))
    assert v1.header.version == 1 and v1.header.based_on is None
    got = store.get(T, "sub_1")
    assert got == v1
    assert got.header.sources[0].fields == ("name",)
    # both files carry the identical header
    again = ArtifactVersion.from_files(got.data_file(), got.factsheet_file())
    assert again.header == got.header


def test_versioning_and_history(store):
    store.put(new())
    v2 = store.put(new(based_on=1, data={"name": "CRM 2"}, reason="rename"))
    assert v2.header.version == 2 and v2.header.based_on == 1
    hist = store.history(T, "sub_1")
    assert [v.header.version for v in hist] == [1, 2]
    assert store.get(T, "sub_1", VersionSelector(1)).data == {"name": "CRM"}
    assert store.latest_version(T, "sub_1") == 2


def test_optimistic_locking_never_overwrites(store):
    store.put(new())
    store.put(new(based_on=1, data={"name": "A"}))
    with pytest.raises(VersionConflict) as exc:
        store.put(new(based_on=1, data={"name": "B"}))
    assert exc.value.details["latest"] == 2
    assert store.get(T, "sub_1").data == {"name": "A"}


def test_first_version_must_have_no_based_on(store):
    with pytest.raises(VersionConflict):
        store.put(new(based_on=3))


def test_status_transitions(store):
    store.put(new(status=ArtifactStatus.SUBMITTED))
    with pytest.raises(InvalidTransition):
        store.put(new(based_on=1, status=ArtifactStatus.APPROVED))
    store.put(new(based_on=1, status=ArtifactStatus.VALIDATED, reason="validated"))
    v3 = store.put(new(based_on=2, status=ArtifactStatus.APPROVED, reason="approved"))
    assert v3.header.content_hash == store.get(T, "sub_1", VersionSelector(1)).header.content_hash


def test_diff_field_level_and_text(store):
    a = store.put(new(data={"name": "CRM", "users": 10, "tech": ["java"]}, body="Line A\n"))
    b = store.put(new(based_on=1, data={"name": "CRM", "users": 20, "tech": ["java", "go"], "x": 1},
                      body="Line B\n"))
    d = store.diff(a, b)
    paths = {(c.path, c.change) for c in d.field_changes}
    assert ("users", "changed") in paths and ("x", "added") in paths and ("tech[1]", "added") in paths
    assert "-Line A" in d.factsheet_diff and "+Line B" in d.factsheet_diff


def test_snapshots_and_as_of(store):
    store.put(new(subject="s1"))
    store.put(new(subject="s2"))
    snap = store.snapshot(T, "cyc_1", "baseline", "usr_1", ["s1", "s2", "s3"])
    assert snap.versions == {"s1": 1, "s2": 1}
    t_mid = utcnow()
    store.put(new(subject="s1", based_on=1, data={"name": "new"}))
    assert store.get(T, "s1", VersionSelector(snapshot="cyc_1/baseline")).header.version == 1
    assert store.get(T, "s1").header.version == 2
    assert {k: v.header.version for k, v in store.as_of(T, "cyc_1", t_mid).items()} == {"s1": 1, "s2": 1}
    assert store.as_of(T, "cyc_1", t_mid - timedelta(days=1)) == {}
    assert [s.name for s in store.list_snapshots(T, "cyc_1")] == ["baseline"]


def test_integrity_check(store):
    store.put(new())
    assert store.verify(T, "sub_1", 1)


def test_tenant_isolation(store):
    store.put(new())
    with pytest.raises(NotFound):
        store.get("ten_b", "sub_1")
