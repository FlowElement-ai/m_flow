"""
Document classification module.

Maps file extensions to corresponding document processor classes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from m_flow.core.domain.models.memory_space import MemorySpace
from m_flow.core.domain.utils.generate_node_id import generate_node_id
from m_flow.data.processing.document_types import (
    AudioDocument,
    CsvDocument,
    Document,
    ImageDocument,
    PdfDocument,
    TextDocument,
    UnstructuredDocument,
)
from m_flow.ingestion.documents.exceptions import WrongDataDocumentInputError

if TYPE_CHECKING:
    from m_flow.data.models import Data

# Text formats
_TEXT_EXTS = {
    "pdf": PdfDocument,
    "txt": TextDocument,
    "csv": CsvDocument,
}

# Office document formats
_OFFICE_EXTS = dict.fromkeys(["docx", "doc", "odt", "xls", "xlsx", "ppt", "pptx", "odp", "ods"], UnstructuredDocument)

# Image formats
_IMAGE_EXTS = dict.fromkeys(
    [
        "png",
        "dwg",
        "xcf",
        "jpg",
        "jpx",
        "apng",
        "gif",
        "webp",
        "cr2",
        "tif",
        "bmp",
        "jxr",
        "psd",
        "ico",
        "heic",
        "avif",
    ],
    ImageDocument,
)

# Audio formats
_AUDIO_EXTS = dict.fromkeys(["aac", "mid", "mp3", "m4a", "ogg", "flac", "wav", "amr", "aiff"], AudioDocument)

# Merged mapping table
_DOC_TYPE_MAP = {**_TEXT_EXTS, **_OFFICE_EXTS, **_IMAGE_EXTS, **_AUDIO_EXTS}


def _parse_memory_spaces(doc: Document) -> None:
    """Parse external metadata and populate associated memory spaces."""
    raw_meta = doc.external_metadata

    try:
        parsed = json.loads(raw_meta)
    except (json.JSONDecodeError, TypeError):
        return

    if not isinstance(parsed, dict):
        return

    space_names = parsed.get("graph_scope")
    if not isinstance(space_names, list):
        return

    doc.memory_spaces = [
        MemorySpace(
            id=generate_node_id(f"MemorySpace:{nm}"),
            name=nm,
        )
        for nm in space_names
    ]


async def detect_format(data_documents: list[Data]) -> list[Document]:
    """
    Classify data items into typed documents.

    Args:
        data_documents: Raw data items to classify.

    Returns:
        List of typed Document instances.

    Raises:
        WrongDataDocumentInputError: Raised when input is not a list.
    """
    if not isinstance(data_documents, list):
        raise WrongDataDocumentInputError("data_documents")

    output: list[Document] = []

    for data in data_documents:
        # Get document type, default to TextDocument
        doc_type = _DOC_TYPE_MAP.get(data.extension, TextDocument)

        # Build metadata JSON
        meta_json = json.dumps(data.external_metadata, indent=4, default=str)

        # Create document instance
        doc = doc_type(
            id=data.id,
            title=f"{data.name}.{data.extension}",
            processed_path=data.processed_path,
            name=data.name,
            mime_type=data.mime_type,
            external_metadata=meta_json,
        )

        # Propagate created_at from Data (datetime) to Document (int ms)
        # This preserves historical timestamps for time-aware memory processing
        if data.created_at is not None:
            doc.created_at = int(data.created_at.timestamp() * 1000)

        _parse_memory_spaces(doc)
        output.append(doc)

    return output
