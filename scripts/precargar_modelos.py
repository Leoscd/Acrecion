"""Descarga por adelantado los modelos de Docling, Whisper y OCR, usando las mismas funciones que el pipeline
(asi quedan en la misma cache que se usa al procesar). Pensado para el build de Docker; nunca falla el build.

Uso: python scripts/precargar_modelos.py
"""
from __future__ import annotations

import sys

from aiaudit.config import CONFIG_DIR, leer_yaml


def docling():
    from docling.datamodel.base_models import InputFormat

    from aiaudit.ingest.documents import _docling
    _docling().initialize_pipeline(InputFormat.PDF)


def whisper():
    from aiaudit.ingest.media import _whisper
    _whisper(leer_yaml(CONFIG_DIR / "settings.yaml")["ingesta"]["whisper_modelo"])


def ocr():
    from aiaudit.ingest.media import _ocr
    _ocr()


def main() -> int:
    fallos = 0
    for nombre, tarea in {"docling": docling, "whisper": whisper, "ocr": ocr}.items():
        try:
            tarea()
            print(f"ok     {nombre}")
        except Exception as e:  # sin red o sin el extra instalado: se descargara al primer uso
            fallos += 1
            print(f"fallo  {nombre}: {type(e).__name__}: {e}", file=sys.stderr)
    return fallos


if __name__ == "__main__":
    n = main()
    print(f"{n} modelo(s) no se pudieron precargar; se descargaran en el primer uso." if n else "Modelos precargados.")
