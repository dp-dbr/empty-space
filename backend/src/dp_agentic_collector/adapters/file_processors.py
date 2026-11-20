"""FileProcessors: content extraction per MIME type (extension point 12)."""

from __future__ import annotations

import base64
import csv
import io
from typing import Any

from ..extensions.interfaces import ExtractedContent, FileProcessor

MAX_CHARS = 200_000


class PdfProcessor(FileProcessor):
    name = "pdf"
    mime_types = ("application/pdf",)

    def extract(self, content: bytes, filename: str, mime_type: str) -> ExtractedContent:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        return ExtractedContent(text=text[:MAX_CHARS], metadata={"pages": len(reader.pages)})


class DocxProcessor(FileProcessor):
    name = "docx"
    mime_types = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",)

    def extract(self, content: bytes, filename: str, mime_type: str) -> ExtractedContent:
        import docx

        doc = docx.Document(io.BytesIO(content))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return ExtractedContent(text="\n".join(parts)[:MAX_CHARS])


class XlsxProcessor(FileProcessor):
    name = "xlsx"
    mime_types = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)

    def extract(self, content: bytes, filename: str, mime_type: str) -> ExtractedContent:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            lines.append(f"# Sheet: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    lines.append(" | ".join("" if v is None else str(v) for v in row))
        return ExtractedContent(text="\n".join(lines)[:MAX_CHARS], metadata={"sheets": len(wb.worksheets)})


class CsvProcessor(FileProcessor):
    name = "csv"
    mime_types = ("text/csv",)

    def extract(self, content: bytes, filename: str, mime_type: str) -> ExtractedContent:
        text = content.decode("utf-8", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
        return ExtractedContent(text="\n".join(" | ".join(r) for r in rows)[:MAX_CHARS], metadata={"rows": len(rows)})


class TextProcessor(FileProcessor):
    name = "text"
    mime_types = ("text/plain", "text/markdown", "application/json")

    def extract(self, content: bytes, filename: str, mime_type: str) -> ExtractedContent:
        return ExtractedContent(text=content.decode("utf-8", errors="replace")[:MAX_CHARS])


class ImageProcessor(FileProcessor):
    """Images are passed to the LLM as image parts where the provider supports it."""

    name = "image"
    mime_types = ("image/png", "image/jpeg")

    def extract(self, content: bytes, filename: str, mime_type: str) -> ExtractedContent:
        return ExtractedContent(text=f"[image {filename}]", image_b64=base64.b64encode(content).decode(),
                                media_type=mime_type)


def register(r: Any) -> None:
    for proc in (PdfProcessor(), DocxProcessor(), XlsxProcessor(), CsvProcessor(), TextProcessor(), ImageProcessor()):
        r.file_processors.register(proc.name, proc)


def processor_for(registry: Any, mime_type: str) -> FileProcessor | None:
    for _, proc in registry.items():
        if mime_type in proc.mime_types:
            return proc  # type: ignore[no-any-return]
    return None


def sniff_mime(content: bytes, filename: str) -> str:
    """Detect the MIME type from content (python-magic), refined by extension for container formats."""
    import magic

    sniffed = magic.from_buffer(content[:8192], mime=True) or "application/octet-stream"
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if sniffed in ("application/zip", "application/octet-stream", "application/vnd.openxmlformats-officedocument"):
        if ext == "docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if ext == "xlsx":
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if sniffed.startswith("text/") or sniffed == "application/csv":
        if ext == "csv":
            return "text/csv"
        if ext in ("md", "markdown"):
            return "text/markdown"
        if ext == "json":
            return "application/json"
        return "text/plain" if sniffed in ("text/plain", "text/x-file") or sniffed.startswith("text/x-") else sniffed
    return sniffed
