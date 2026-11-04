"""Secuencia completa del pipeline, reutilizada por la CLI y por el worker del portal."""
from __future__ import annotations

from typing import Callable

from aiaudit.config import Cliente

ETAPAS = ["ingest", "anonymize", "analyze", "score", "report"]


def _ingest(c):
    from aiaudit.ingest.router import ingerir
    return {"ingest": ingerir(c)}


def _anonymize(c):
    from aiaudit.privacy.anonymize import anonimizar
    return {"anonymize": anonimizar(c)} if c.settings["privacidad"]["anonimizar"] else {}


def _analyze(c):
    from aiaudit.analyze import consolidate, process_extract, process_mining
    return {"extraccion": process_extract.extraer(c), "mining": process_mining.minar(c),
            "consolidacion": consolidate.consolidar(c)}


def _score(c):
    from aiaudit.score.opportunity import priorizar
    return {"score": priorizar(c)}


def _report(c):
    from aiaudit.report import generar
    return {"report": generar(c)}


FUNCIONES = {"ingest": _ingest, "anonymize": _anonymize, "analyze": _analyze, "score": _score, "report": _report}


def ejecutar(cliente: Cliente, desde: str = "ingest", al_avanzar: Callable[[str, dict], None] | None = None) -> dict:
    """Corre las etapas desde 'desde'. al_avanzar(etapa, resultado) se llama al terminar cada una."""
    if desde not in ETAPAS:
        raise ValueError(f"'desde' debe ser una de {ETAPAS}")
    resumen = {}
    for etapa in ETAPAS[ETAPAS.index(desde):]:
        resultado = FUNCIONES[etapa](cliente)
        resumen.update(resultado)
        if al_avanzar:
            al_avanzar(etapa, resultado)
    return resumen
