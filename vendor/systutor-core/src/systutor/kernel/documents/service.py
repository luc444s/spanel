from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from systutor.contracts.events import EventContract
from systutor.core.config import PROJECT_ROOT
from systutor.kernel.documents.models import CoreDocumentVersion
from systutor.kernel.events.service import emit_event

DOCUMENT_STORAGE_ROOT = PROJECT_ROOT / "data" / "media" / "documents"


def build_document_download_url(document_version_id: str) -> str:
    return f"/api/v1/core/documents/{document_version_id}/download"


def get_document_version(db: Session, *, document_version_id: str) -> CoreDocumentVersion | None:
    return db.scalar(
        select(CoreDocumentVersion).where(CoreDocumentVersion.id == document_version_id)
    )


def list_document_versions_for_entity(
    db: Session,
    *,
    tenant_id: str,
    module: str,
    entity_type: str,
    entity_id: str,
) -> list[CoreDocumentVersion]:
    return list(
        db.scalars(
            select(CoreDocumentVersion)
            .where(
                CoreDocumentVersion.tenant_id == tenant_id,
                CoreDocumentVersion.module == module,
                CoreDocumentVersion.entity_type == entity_type,
                CoreDocumentVersion.entity_id == entity_id,
            )
            .order_by(CoreDocumentVersion.version_number.desc())
        ).all()
    )


def get_latest_document_version_for_entity(
    db: Session,
    *,
    tenant_id: str,
    module: str,
    entity_type: str,
    entity_id: str,
) -> CoreDocumentVersion | None:
    return db.scalar(
        select(CoreDocumentVersion)
        .where(
            CoreDocumentVersion.tenant_id == tenant_id,
            CoreDocumentVersion.module == module,
            CoreDocumentVersion.entity_type == entity_type,
            CoreDocumentVersion.entity_id == entity_id,
        )
        .order_by(CoreDocumentVersion.version_number.desc())
        .limit(1)
    )


def resolve_document_file_path(document_version: CoreDocumentVersion) -> Path:
    return DOCUMENT_STORAGE_ROOT / document_version.id / Path(document_version.file_path).name


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_simple_pdf(title: str, lines: list[str]) -> bytes:
    content_lines = [title, *lines]
    stream_lines = ["BT", "/F1 12 Tf", "50 780 Td", "14 TL"]
    for index, line in enumerate(content_lines):
        if index > 0:
            stream_lines.append("T*")
        stream_lines.append(f"({_escape_pdf_text(line)}) Tj")
    stream_lines.append("ET")
    content = "\n".join(stream_lines).encode("latin-1", errors="replace")

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
        ),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        (
            b"5 0 obj\n<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream\nendobj\n"
        ),
    ]

    parts: list[bytes] = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets: list[int] = []
    for obj in objects:
        offsets.append(sum(len(part) for part in parts))
        parts.append(obj)

    xref_start = sum(len(part) for part in parts)
    xref_lines = [f"xref\n0 {len(objects) + 1}\n", "0000000000 65535 f \n"]
    xref_lines.extend(f"{offset:010d} 00000 n \n" for offset in offsets)
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF"
    parts.append("".join(xref_lines).encode())
    parts.append(trailer.encode())
    return b"".join(parts)


def _coerce_payload_lines(payload: dict) -> tuple[str, list[str]]:
    title = str(payload.get("document_title") or payload.get("title") or "Documento")
    if isinstance(payload.get("document_lines"), list):
        lines = [str(item) for item in payload["document_lines"]]
        return title, lines

    lines: list[str] = []
    for key, value in payload.items():
        if key in {"document_title", "title"}:
            continue
        lines.append(f"{key}: {value}")
    return title, lines


def render_document_pdf(
    db: Session,
    *,
    tenant_id: str,
    module: str,
    entity_type: str,
    entity_id: str,
    template_code: str,
    payload: dict,
    created_by: str | None,
    status: str = "DRAFT",
) -> CoreDocumentVersion:
    latest_version = db.scalar(
        select(func.max(CoreDocumentVersion.version_number)).where(
            CoreDocumentVersion.tenant_id == tenant_id,
            CoreDocumentVersion.module == module,
            CoreDocumentVersion.entity_type == entity_type,
            CoreDocumentVersion.entity_id == entity_id,
        )
    )
    version_number = int(latest_version or 0) + 1
    title, lines = _coerce_payload_lines(payload)
    pdf_bytes = _build_simple_pdf(title, lines)
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    document = CoreDocumentVersion(
        tenant_id=tenant_id,
        module=module,
        entity_type=entity_type,
        entity_id=entity_id,
        template_code=template_code,
        version_number=version_number,
        status=status,
        title=title,
        file_path=f"v{version_number}.pdf",
        sha256=sha256,
        created_by=created_by,
    )
    db.add(document)
    db.flush()

    document_dir = DOCUMENT_STORAGE_ROOT / document.id
    document_dir.mkdir(parents=True, exist_ok=True)
    resolve_document_file_path(document).write_bytes(pdf_bytes)

    emit_event(
        db,
        event=EventContract(
            event_name="core.document.rendered",
            module="core",
            tenant_id=tenant_id,
            actor_user_id=created_by,
            actor_type="user" if created_by else "system",
            entity_type=entity_type,
            entity_id=entity_id,
            payload={
                "document_version_id": document.id,
                "template_code": template_code,
                "version_number": version_number,
                "status": status,
            },
        ),
    )

    return document
