"""Lectura de un libro Excel como modelo de calculo: formulas, valores, referencias y grafo de dependencias.

Es generico (sirve para cualquier planilla). El analisis de presupuestos se construye encima.
Usa openpyxl dos veces: con formulas (data_only=False) y con los valores que Excel dejo cacheados (data_only=True).
Si el libro no tiene valores cacheados (lo genero un software que no calcula), intenta recalcular con LibreOffice.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import openpyxl
from openpyxl.formula import Tokenizer
from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries

ERRORES = {"#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NUM!", "#NULL!"}
PATRON_REF = re.compile(
    r"^(?:\[(?P<ext>[^\]]+)\])?(?:'?(?P<hoja>[^'!]+)'?!)?(?P<rango>\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)$")
MAX_CELDAS_RANGO = 5000  # rangos mas grandes no se expanden en el grafo (solo se cuentan)


@dataclass
class Referencia:
    hoja: str
    rango: str
    externa: str | None = None


@dataclass
class Celda:
    hoja: str
    coord: str
    fila: int
    col: int
    formula: str | None
    valor: object
    referencias: list[Referencia] = field(default_factory=list)
    numeros: list[float] = field(default_factory=list)  # literales numericos dentro de la formula

    @property
    def id(self) -> str:
        return f"{self.hoja}!{self.coord}"

    @property
    def es_error(self) -> bool:
        return (isinstance(self.valor, str) and self.valor in ERRORES) or bool(self.formula and "#REF!" in self.formula)


@dataclass
class ModeloExcel:
    ruta: Path
    celdas: dict[str, Celda]                 # "Hoja!A1" -> Celda
    hojas: list[str]
    hojas_ocultas: list[str]
    vinculos_externos: int
    recalculado: bool
    grafo: nx.DiGraph                        # precedente -> dependiente

    def celda(self, hoja: str, coord: str) -> Celda | None:
        return self.celdas.get(f"{hoja}!{coord.replace('$', '')}")

    def de_hoja(self, hoja: str) -> list[Celda]:
        return [c for c in self.celdas.values() if c.hoja == hoja]

    @property
    def formulas(self) -> list[Celda]:
        return [c for c in self.celdas.values() if c.formula]

    def sin_valores(self) -> bool:
        f = self.formulas
        return bool(f) and all(c.valor is None for c in f)

    def profundidad(self) -> int:
        """Cadena de calculo mas larga (insumo -> ... -> total). 0 si hay ciclos."""
        return nx.dag_longest_path_length(self.grafo) if nx.is_directed_acyclic_graph(self.grafo) else 0

    def ciclos(self, maximo: int = 10) -> list[list[str]]:
        salida = []
        for ciclo in nx.simple_cycles(self.grafo):
            salida.append(ciclo)
            if len(salida) >= maximo:
                break
        return salida


def _parsear(formula: str, hoja_actual: str) -> tuple[list[Referencia], list[float]]:
    refs, numeros = [], []
    try:
        tokens = Tokenizer(formula).items
    except Exception:
        return refs, numeros
    for t in tokens:
        if t.type != "OPERAND":
            continue
        if t.subtype == "NUMBER":
            try:
                numeros.append(float(t.value))
            except ValueError:
                pass
        elif t.subtype == "RANGE":
            m = PATRON_REF.match(t.value)
            if m:
                refs.append(Referencia(m["hoja"] or hoja_actual, m["rango"].replace("$", ""), m["ext"]))
    return refs, numeros


def _expandir(rango: str) -> list[str]:
    min_c, min_f, max_c, max_f = range_boundaries(rango)
    if (max_c - min_c + 1) * (max_f - min_f + 1) > MAX_CELDAS_RANGO:
        return []
    return [f"{get_column_letter(c)}{f}" for f in range(min_f, max_f + 1) for c in range(min_c, max_c + 1)]


def recalcular_libreoffice(ruta: Path, destino_dir: Path) -> Path | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    r = subprocess.run([soffice, "--headless", "--calc", "--convert-to", "xlsx", "--outdir", str(destino_dir), str(ruta)],
                       capture_output=True, timeout=300, stdin=subprocess.DEVNULL)
    salida = destino_dir / (ruta.stem + ".xlsx")
    return salida if r.returncode == 0 and salida.exists() else None


def cargar(ruta: Path) -> ModeloExcel:
    ruta = Path(ruta)
    wb_f = openpyxl.load_workbook(ruta, data_only=False)
    wb_v = openpyxl.load_workbook(ruta, data_only=True)
    modelo = _construir(ruta, wb_f, wb_v, recalculado=False)
    if modelo.sin_valores():
        with tempfile.TemporaryDirectory() as tmp:
            recalculado = recalcular_libreoffice(ruta, Path(tmp))
            if recalculado:
                modelo = _construir(ruta, wb_f, openpyxl.load_workbook(recalculado, data_only=True), recalculado=True)
    return modelo


def _construir(ruta: Path, wb_f, wb_v, recalculado: bool) -> ModeloExcel:
    celdas: dict[str, Celda] = {}
    for ws in wb_f.worksheets:
        valores = wb_v[ws.title]
        for fila in ws.iter_rows():
            for c in fila:
                if c.value is None:
                    continue
                formula = c.value if isinstance(c.value, str) and c.value.startswith("=") else None
                if formula is None and hasattr(c.value, "text"):  # formulas matriciales
                    formula = str(c.value.text)
                valor = valores[c.coordinate].value if formula else c.value
                refs, nums = _parsear(formula, ws.title) if formula else ([], [])
                celda = Celda(ws.title, c.coordinate, c.row, c.column, formula, valor, refs, nums)
                celdas[celda.id] = celda

    grafo = nx.DiGraph()
    for c in celdas.values():
        if not c.formula:
            continue
        grafo.add_node(c.id)
        for ref in c.referencias:
            if ref.externa:
                grafo.add_edge(f"[{ref.externa}]{ref.hoja}!{ref.rango}", c.id)
                continue
            for coord in _expandir(ref.rango):
                grafo.add_edge(f"{ref.hoja}!{coord}", c.id)

    return ModeloExcel(
        ruta=ruta, celdas=celdas, hojas=[ws.title for ws in wb_f.worksheets],
        hojas_ocultas=[ws.title for ws in wb_f.worksheets if ws.sheet_state != "visible"],
        vinculos_externos=len(getattr(wb_f, "_external_links", []) or []), recalculado=recalculado, grafo=grafo,
    )


def firma_relativa(c: Celda) -> str:
    """Formula expresada en coordenadas relativas (estilo R1C1): '=D7*E7' en F7 -> '=RC[-2]*RC[-1]'.
    Dos celdas de una columna con la misma logica tienen la misma firma."""
    if not c.formula:
        return ""

    def reemplazo(m: re.Match) -> str:
        col_abs, col, fila_abs, fila = m.group(1), m.group(2), m.group(3), int(m.group(4))
        ci = column_index_from_string(col)
        r = f"R{fila}" if fila_abs else (f"R[{fila - c.fila}]" if fila != c.fila else "R")
        k = f"C{ci}" if col_abs else (f"C[{ci - c.col}]" if ci != c.col else "C")
        return r + k

    return re.sub(r"(\$?)([A-Z]{1,3})(\$?)(\d+)", reemplazo, c.formula)
