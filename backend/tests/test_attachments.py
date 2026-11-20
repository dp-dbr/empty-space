import io

import pytest
from fastapi.testclient import TestClient

from dp_agentic_collector.api.app import create_app
from dp_agentic_collector.domain.enums import AttachmentStatus
from dp_agentic_collector.domain.errors import (
    LimitExceeded,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)

NOTE = b"Inventory note\nnumber_of_users: 420\ntechnical_owner: Team Data\n"


def make_docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph("criticality: low")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["name", "users"])
    wb.active.append(["CRM", 12])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def test_private_attachment_visibility_and_confirmation(app, world, cycle_setup):
    _, subject, job = cycle_setup
    s = app.services
    alice, bob, owner, viewer = (world.actor(u) for u in (world.alice, world.bob, world.owner, world.viewer))
    att = s.attachments.upload(alice, job.job_id, "../../etc/note.txt", NOTE)
    assert att.filename == "note.txt" and att.visibility == "private" and att.mime_type == "text/plain"
    app.drain()  # extraction in worker
    assert "420" in s.attachments.get(alice, att.attachment_id).extracted_text

    for other in (bob, owner, viewer):
        with pytest.raises(NotFound):
            s.attachments.get(other, att.attachment_id)
        assert all(a.attachment_id != att.attachment_id for a in s.attachments.list_for_subject(other, subject.subject_id)
                   ) if other is not bob else True

    # agent reads the private file and proposes facts that need a visibility confirmation
    s.conversations.post_user_message(alice, job.job_id, "please use the attachment", [att.attachment_id])
    app.drain()
    props = s.conversations.proposals(alice, job.job_id, status="proposed")
    assert {p.path for p in props} == {"number_of_users", "technical_owner"}
    assert all(p.requires_visibility_confirmation for p in props)
    assert s.conversations.proposals(owner, job.job_id) == []  # not visible to others
    with pytest.raises(ValidationFailed):
        s.conversations.decide_proposal(alice, props[0].proposal_id, "confirm")
    for p in props:
        s.conversations.decide_proposal(alice, p.proposal_id, "confirm", visibility_ack=True)
    s.conversations.post_user_message(alice, job.job_id, "name: CRM\nbusiness_owner: Sales\ncriticality: low")
    app.drain()
    for p in s.conversations.proposals(alice, job.job_id, status="proposed"):
        s.conversations.decide_proposal(alice, p.proposal_id, "confirm")
    v = s.artifacts.submit(alice, job.job_id).version
    src = [x for x in v.header.sources if x.kind == "attachment"]
    assert src and src[0].content_hash == att.content_hash and src[0].attachment_id == att.attachment_id
    assert "note.txt" not in v.data_file()  # provenance records the hash, not the file

    # visibility change: private -> shared makes it visible to readers of the subject
    s.attachments.share(alice, att.attachment_id)
    assert s.attachments.get(owner, att.attachment_id).visibility == "shared"
    with pytest.raises(NotFound):
        s.attachments.get(bob, att.attachment_id)  # bob cannot read the subject
    # withdraw: invisible to others again, uploader still sees it
    s.attachments.withdraw(alice, att.attachment_id)
    with pytest.raises(NotFound):
        s.attachments.get(owner, att.attachment_id)
    assert s.attachments.get(alice, att.attachment_id).status == AttachmentStatus.WITHDRAWN


def test_delete_private_erases_content_keeps_metadata(app, world, cycle_setup):
    _, _, job = cycle_setup
    s = app.services
    alice, owner = world.actor(world.alice), world.actor(world.owner)
    att = s.attachments.upload(alice, job.job_id, "secret.md", b"# private notes\n")
    with pytest.raises(NotFound):  # even admins cannot see or delete someone else's private file
        s.attachments.delete(world.actor(world.admin), att.attachment_id)
    with pytest.raises(NotFound):
        s.attachments.share(owner, att.attachment_id)
    s.attachments.delete(alice, att.attachment_id)
    assert not app.attachment_store.exists(world.tenant.tenant_id, att.content_hash)
    with pytest.raises(NotFound):
        s.attachments.content(alice, att.attachment_id)
    log = s.tenancy.audit_log(world.actor(world.admin))
    assert any(e.action == "attachment.delete" and att.attachment_id in e.resource for e in log)


def test_limits_and_mime_allowlist(app, world, cycle_setup):
    _, _, job = cycle_setup
    alice = world.actor(world.alice)
    with pytest.raises(ValidationFailed):
        app.services.attachments.upload(alice, job.job_id, "x.html", b"<html><script>alert(1)</script></html>")
    with pytest.raises(ValidationFailed):
        app.services.attachments.upload(alice, job.job_id, "x.exe", b"MZ\x90\x00" + b"\x00" * 100)
    app.settings.attachment_max_bytes = 10
    with pytest.raises(LimitExceeded):
        app.services.attachments.upload(alice, job.job_id, "big.txt", b"x" * 11)
    with pytest.raises((NotFound, PermissionDenied)):
        app.services.attachments.upload(world.actor(world.bob), job.job_id, "a.txt", b"hello")


@pytest.mark.parametrize("name,maker,expect", [
    ("a.docx", make_docx, "criticality: low"),
    ("a.xlsx", make_xlsx, "CRM | 12"),
    ("a.pdf", lambda: make_pdf("Hello PDF"), "Hello PDF"),
    ("a.csv", lambda: b"a,b\n1,2\n", "1 | 2"),
])
def test_file_processors(app, world, cycle_setup, name, maker, expect):
    _, _, job = cycle_setup
    alice = world.actor(world.alice)
    att = app.services.attachments.upload(alice, job.job_id, name, maker())
    att = app.services.attachments.process(alice, att.attachment_id)
    assert expect in att.extracted_text


def test_api_upload_and_safe_serving(app, world, cycle_setup):
    _, _, job = cycle_setup
    client = TestClient(create_app(app, start_worker=False))
    csrf = client.post("/api/v1/auth/login", json={"email": "alice@default.test", "password": "pw-alice-123"}).json()["csrf_token"]
    r = client.post(f"/api/v1/jobs/{job.job_id}/attachments", headers={"X-CSRF-Token": csrf},
                    files=[("files", ("one.txt", b"one", "text/plain")), ("files", ("two.md", b"# two", "text/markdown"))],
                    data={"visibility": "shared"})
    assert r.status_code == 201, r.text
    ids = [a["attachment_id"] for a in r.json()]
    assert [a["visibility"] for a in r.json()] == ["shared", "shared"]
    c = client.get(f"/api/v1/attachments/{ids[0]}/content")
    assert c.content == b"one" and c.headers["content-disposition"].startswith("attachment")
    assert c.headers["x-content-type-options"] == "nosniff"
    # bob (no access) gets 404 via API
    client.cookies.clear()
    client.post("/api/v1/auth/login", json={"email": "bob@default.test", "password": "pw-bob-123"})
    assert client.get(f"/api/v1/attachments/{ids[0]}").status_code == 404
