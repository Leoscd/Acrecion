"""Audio/video -> texto (faster-whisper) e imagenes -> texto (RapidOCR, modelos de PaddleOCR en ONNX)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=2)
def _whisper(modelo: str):
    from faster_whisper import WhisperModel
    return WhisperModel(modelo, device="cpu", compute_type="int8")


@lru_cache(maxsize=1)
def _ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def transcribir(ruta: Path, modelo: str = "small", idioma: str | None = "es") -> tuple[str, dict]:
    """Transcripcion con marcas de tiempo [mm:ss]. Devuelve (texto, metadatos)."""
    segmentos, info = _whisper(modelo).transcribe(str(ruta), language=idioma, vad_filter=True)
    lineas = [f"[{int(s.start // 60):02d}:{int(s.start % 60):02d}] {s.text.strip()}" for s in segmentos]
    return "\n".join(lineas), {"duracion_s": round(info.duration, 1), "idioma": info.language}


def ocr_imagen(ruta: Path, min_score: float = 0.5) -> tuple[str, dict]:
    """OCR de imagen ordenado de arriba a abajo. Devuelve (texto, metadatos)."""
    resultado, _ = _ocr()(str(ruta))
    if not resultado:
        return "", {"lineas_ocr": 0}
    # cada resultado: [caja, texto, score]; el score puede venir como str segun la version
    lineas = sorted((r for r in resultado if float(r[2]) >= min_score), key=lambda r: (r[0][0][1], r[0][0][0]))
    return "\n".join(r[1] for r in lineas), {"lineas_ocr": len(lineas)}
