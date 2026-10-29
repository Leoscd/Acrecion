"""Documentos Office/PDF/HTML/texto -> Markdown. Docling (estructura y tablas) con fallback a MarkItDown."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

# Formatos donde docling aporta (layout, tablas, OCR de PDFs escaneados)
EXT_DOCLING = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm"}


@lru_cache(maxsize=1)
def _docling():
    from docling.document_converter import DocumentConverter
    return DocumentConverter()


@lru_cache(maxsize=1)
def _markitdown():
    from markitdown import MarkItDown
    return MarkItDown()


def a_markdown(ruta: Path, motor: str = "auto") -> tuple[str, str]:
    """Devuelve (markdown, motor_usado)."""
    if motor in ("auto", "docling") and ruta.suffix.lower() in EXT_DOCLING:
        try:
            return _docling().convert(str(ruta)).document.export_to_markdown(), "docling"
        except ImportError:
            if motor == "docling":
                raise
        except Exception as e:  # docling falla en algunos archivos: se intenta con markitdown
            log.warning("docling fallo en %s: %s", ruta.name, e)
    return _markitdown().convert(str(ruta)).text_content, "markitdown"
