from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from ..domain.enums import AttachmentVisibility
from ..domain.errors import LimitExceeded
from .deps import Ctx, get_ctx
from .schemas import AttachmentOut

router = APIRouter(tags=["attachments"])


@router.post("/jobs/{job_id}/attachments", response_model=list[AttachmentOut], status_code=201)
async def upload(job_id: str, files: list[UploadFile] = File(...), visibility: str = Form("private"),
                 ctx: Ctx = Depends(get_ctx)) -> list[AttachmentOut]:
    out = []
    limit = ctx.app.settings.attachment_max_bytes
    for f in files:
        content = await f.read(limit + 1)
        if len(content) > limit:
            raise LimitExceeded(f"{f.filename} exceeds {limit} bytes")
        att = ctx.s.attachments.upload(ctx.actor, job_id, f.filename or "file", content,
                                       AttachmentVisibility(visibility))
        out.append(AttachmentOut.of(att))
    return out


@router.get("/jobs/{job_id}/attachments", response_model=list[AttachmentOut])
def job_attachments(job_id: str, ctx: Ctx = Depends(get_ctx)) -> list[AttachmentOut]:
    return [AttachmentOut.of(a) for a in ctx.s.attachments.list_for_job(ctx.actor, job_id)]


@router.get("/subjects/{subject_id}/attachments", response_model=list[AttachmentOut])
def subject_attachments(subject_id: str, ctx: Ctx = Depends(get_ctx)) -> list[AttachmentOut]:
    return [AttachmentOut.of(a) for a in ctx.s.attachments.list_for_subject(ctx.actor, subject_id)]


@router.get("/attachments/{attachment_id}", response_model=AttachmentOut)
def get_attachment(attachment_id: str, ctx: Ctx = Depends(get_ctx)) -> AttachmentOut:
    return AttachmentOut.of(ctx.s.attachments.get(ctx.actor, attachment_id))


@router.get("/attachments/{attachment_id}/content")
def content(attachment_id: str, ctx: Ctx = Depends(get_ctx)) -> Response:
    att, data = ctx.s.attachments.content(ctx.actor, attachment_id)
    # safe serving: always as download, no sniffing, sandboxed
    return Response(content=data, media_type=att.mime_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(att.filename)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, no-store",
    })


@router.post("/attachments/{attachment_id}/share", response_model=AttachmentOut)
def share(attachment_id: str, ctx: Ctx = Depends(get_ctx)) -> AttachmentOut:
    return AttachmentOut.of(ctx.s.attachments.share(ctx.actor, attachment_id))


@router.post("/attachments/{attachment_id}/withdraw", response_model=AttachmentOut)
def withdraw(attachment_id: str, ctx: Ctx = Depends(get_ctx)) -> AttachmentOut:
    return AttachmentOut.of(ctx.s.attachments.withdraw(ctx.actor, attachment_id))


@router.delete("/attachments/{attachment_id}", response_model=AttachmentOut)
def delete(attachment_id: str, ctx: Ctx = Depends(get_ctx)) -> AttachmentOut:
    return AttachmentOut.of(ctx.s.attachments.delete(ctx.actor, attachment_id))
