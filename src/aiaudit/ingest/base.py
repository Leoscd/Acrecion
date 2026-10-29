"""Utilidades comunes de ingesta."""
from __future__ import annotations

import re
from pathlib import Path

from aiaudit.schemas import Fragmento, TipoFuente


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")[:60]


def partir_texto(texto: str, max_chars: int) -> list[str]:
    """Parte por parrafos sin superar max_chars por bloque."""
    bloques, actual = [], ""
    for parrafo in re.split(r"\n\s*\n", texto):
        parrafo = parrafo.strip()
        if not parrafo:
            continue
        while len(parrafo) > max_chars:  # parrafo gigante: corte duro
            bloques.append(parrafo[:max_chars])
            parrafo = parrafo[max_chars:]
        if len(actual) + len(parrafo) + 2 > max_chars and actual:
            bloques.append(actual)
            actual = ""
        actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    if actual:
        bloques.append(actual)
    return bloques


def fragmentar(texto: str, origen: str, tipo: TipoFuente, max_chars: int, **campos) -> list[Fragmento]:
    base = slug(origen)
    return [
        Fragmento(id=f"{base}#{i}", origen=origen, tipo_fuente=tipo, texto=t, **campos)
        for i, t in enumerate(partir_texto(texto, max_chars))
    ]


def rel(ruta: Path, raiz: Path) -> str:
    return str(ruta.relative_to(raiz))
