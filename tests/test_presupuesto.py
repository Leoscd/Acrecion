"""Auditoria de presupuestos: indicadores exactos y deteccion de cada error sembrado en el libro de demo."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import openpyxl
import pytest

from aiaudit.construccion.excel_modelo import cargar, firma_relativa
from aiaudit.construccion.presupuesto import analizar
from aiaudit.report.auditoria_presupuesto import generar_docx

RAIZ = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("demo_presupuesto", RAIZ / "scripts" / "demo_presupuesto.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)
CON_LIBREOFFICE = bool(shutil.which("soffice") or shutil.which("libreoffice"))


@pytest.fixture(scope="module")
def analisis(tmp_path_factory):
    ruta = demo.crear(tmp_path_factory.mktemp("pres") / "presupuesto.xlsx")
    return analizar(ruta)


def _tipos(a, tipo):
    return [h for h in a.hallazgos if h.tipo == tipo]


def test_reconoce_estructura(analisis):
    assert analisis.hojas == {"parametros": "Parametros", "insumos": "Insumos", "apu": "APU",
                              "presupuesto": "Presupuesto", "resumen": "Resumen"}
    assert analisis.modelo["apu"] == 8 and analisis.modelo["profundidad_calculo"] >= 5
    assert len(analisis.partidas) == 14


@pytest.mark.skipif(not CON_LIBREOFFICE, reason="sin LibreOffice el libro sintetico no tiene valores calculados")
def test_indicadores_exactos(analisis):
    esp, m = demo.esperado(), analisis.metricas
    assert m["costo_directo"] == pytest.approx(esp["costo_directo"], abs=1)
    assert m["total"] == pytest.approx(esp["total"], abs=1)
    assert m["costo_m2"] == pytest.approx(esp["costo_m2"], abs=0.01)
    assert m["venta_m2"] == pytest.approx(esp["venta_m2"], abs=0.01)
    assert sum(analisis.composicion.values()) == pytest.approx(esp["costo_directo"], abs=1)
    a = [p for p in analisis.partidas if p.clase == "A"]
    assert sum(p.participacion for p in a) >= 0.8 > sum(p.participacion for p in a) - min(p.participacion for p in a)


@pytest.mark.skipif(not CON_LIBREOFFICE, reason="los precios unitarios se leen de los valores calculados")
def test_detecta_todos_los_errores_sembrados(analisis):
    assert set(demo.SEMBRADOS) <= {h.tipo for h in analisis.hallazgos}
    assert any("2.5" in c for h in _tipos(analisis, "pu_sin_apu") for c in h.celdas)
    assert _tipos(analisis, "formula_inconsistente")[0].celdas == ["F11"]  # partida 2.4 con *1.05
    assert {0.19, 1.05} <= {float(h.descripcion.split()[2]) for h in _tipos(analisis, "numero_magico")}
    assert any("Pintura muros" in h.descripcion for h in _tipos(analisis, "partida_duplicada"))
    unidades = " ".join(h.descripcion + " ".join(h.celdas) for h in _tipos(analisis, "unidad_inconsistente"))
    assert "Gl" in unidades and "Cemento" in unidades
    assert {h.hoja for h in _tipos(analisis, "hoja_oculta")} == {"Precios_viejos"}
    sin_uso = " ".join(h.descripcion for h in _tipos(analisis, "apu_sin_uso"))
    assert "Estuco" in sin_uso and "Albañilería" in sin_uso
    assert any(h.hoja == "APU" for h in _tipos(analisis, "error_formula"))


def test_excel_que_no_es_presupuesto(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.append(["Nombre", "Telefono"])
    wb.active.append(["Ana", "123"])
    wb.save(tmp_path / "contactos.xlsx")
    assert analizar(tmp_path / "contactos.xlsx") is None


def test_firma_relativa_y_grafo(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "H"
    for f in range(1, 4):
        ws.cell(f, 1, f)
        ws.cell(f, 2, f * 2)
        ws.cell(f, 3, f"=A{f}*B{f}")
    ws["D1"] = "=SUM(C1:C3)"
    wb.save(tmp_path / "g.xlsx")
    m = cargar(tmp_path / "g.xlsx")
    assert len({firma_relativa(m.celda("H", f"C{f}")) for f in range(1, 4)}) == 1
    assert m.profundidad() == 2  # A1 -> C1 -> D1


def test_informe(analisis):
    doc = generar_docx("Demo", [analisis.model_dump()], "$")
    texto = "\n".join(p.text for p in doc.paragraphs) + "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    assert doc.paragraphs[0].text == "Auditoría de presupuestos"
    assert "Claude for Excel" in texto and "Partidas críticas" in texto
    assert "Presupuesto Presupuesto" not in texto
