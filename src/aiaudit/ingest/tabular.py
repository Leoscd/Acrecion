"""Tablas (Excel/CSV) -> resumen textual por hoja + deteccion de logs de eventos para process mining."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# Heuristicas por nombre de columna para reconocer un log de eventos (caso, actividad, fecha)
PATRON_CASO = re.compile(r"^(id|caso|case|folio|n[°ºo]?\s*|numero|orden|pedido|ticket|obra|proyecto|solicitud)", re.I)
PATRON_ACTIVIDAD = re.compile(r"(actividad|activity|etapa|estado|status|tarea|paso|accion|evento)", re.I)
PATRON_FECHA = re.compile(r"(fecha|date|timestamp|hora|time|inicio|registro)", re.I)
PATRON_RECURSO = re.compile(r"(responsable|usuario|user|resource|encargado|asignado)", re.I)


def leer_hojas(ruta: Path) -> dict[str, pd.DataFrame]:
    ext = ruta.suffix.lower()
    if ext in (".csv", ".tsv"):
        sep = "\t" if ext == ".tsv" else None  # None + engine python = autodeteccion del separador
        return {ruta.stem: pd.read_csv(ruta, sep=sep, engine="python", encoding_errors="replace")}
    return pd.read_excel(ruta, sheet_name=None)


def _columna(df: pd.DataFrame, patron: re.Pattern) -> str | None:
    return next((c for c in df.columns if patron.search(str(c))), None)


def detectar_log_eventos(df: pd.DataFrame) -> pd.DataFrame | None:
    """Si la tabla tiene caso + actividad + fecha, la normaliza a case_id/activity/timestamp[/resource]."""
    act, fecha = _columna(df, PATRON_ACTIVIDAD), _columna(df, PATRON_FECHA)
    caso = next((c for c in df.columns if PATRON_CASO.search(str(c)) and c not in (act, fecha)), None)
    if not (caso and act and fecha):
        return None
    ts = pd.to_datetime(df[fecha], errors="coerce", dayfirst=True, format="mixed")
    if ts.notna().mean() < 0.8 or df[caso].nunique() < 2:
        return None
    log = pd.DataFrame({"case_id": df[caso].astype(str), "activity": df[act].astype(str), "timestamp": ts})
    if rec := _columna(df, PATRON_RECURSO):
        log["resource"] = df[rec].astype(str)
    return log.dropna(subset=["timestamp"]).sort_values(["case_id", "timestamp"])


def resumir(nombre: str, df: pd.DataFrame, filas_muestra: int = 8) -> str:
    """Resumen legible por un LLM: estructura, tipos, nulos y muestra de filas."""
    df = df.dropna(how="all").dropna(axis=1, how="all")
    cols = "\n".join(
        f"- {c} ({df[c].dtype}, {df[c].isna().mean():.0%} nulos, {df[c].nunique()} unicos)" for c in df.columns
    )
    muestra = df.head(filas_muestra).to_markdown(index=False) if len(df) else "(vacia)"
    return f"Tabla: {nombre}\nFilas: {len(df)} | Columnas: {len(df.columns)}\n\nColumnas:\n{cols}\n\nMuestra:\n{muestra}"
