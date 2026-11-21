"""Erasure strategy (ADR-0011): personal data removed, immutable artifacts keep only IDs."""

from dp_agentic_collector.domain.enums import AttachmentStatus
from tests.conftest import confirm_all


def test_erase_user(app, world, cycle_setup):
    _, subject, job = cycle_setup
    s = app.services
    alice, admin = world.actor(world.alice), world.actor(world.admin)
    s.conversations.post_user_message(alice, job.job_id, "name: CRM\nbusiness_owner: Sales\ncriticality: low")
    app.drain()
    confirm_all(app, alice, job.job_id)
    version = s.artifacts.submit(alice, job.job_id).version
    att = s.attachments.upload(alice, job.job_id, "private.txt", b"private notes of alice")
    s.tenancy.erase_user(admin, world.alice.user_id)

    user = s.tenancy.get_user(admin, world.alice.user_id)
    assert user.erased and not user.active and user.name == "erased user" and "alice" not in user.email
    # artifacts reference the user by ID only, so they stay intact and verifiable
    assert "Alice" not in version.data_file() and "alice@" not in version.factsheet_file()
    assert app.artifact_store.verify(world.tenant.tenant_id, subject.subject_id, 1)
    # private attachment content erased, metadata kept
    assert not app.attachment_store.exists(world.tenant.tenant_id, att.content_hash)
    from dp_agentic_collector.domain.entities import Attachment

    with app.db.uow(world.tenant.tenant_id) as uow:
        assert uow.repo(Attachment).get(att.attachment_id).status == AttachmentStatus.DELETED
    # messages tombstoned
    msgs = s.conversations.messages(world.actor(world.owner), job.job_id)
    assert all(m.text == "[erased]" for m in msgs if m.sender.ref == world.alice.user_id)
    assert any(e.action == "user.erase" for e in s.tenancy.audit_log(admin))
