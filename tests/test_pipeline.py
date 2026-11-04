"""Tests de punta a punta sobre la demo de constructora. Sin API: el LLM se simula con FakeLLM."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aiaudit.analyze import consolidate, process_extract, process_mining
from aiaudit.config import Cliente, leer_jsonl
from aiaudit.ingest.router import ingerir
from aiaudit.privacy.anonymize import Anonimizador, anonimizar, revertir
from aiaudit.report import generar
from aiaudit.schemas import ExtraccionLLM, Fragmento, PasoLLM, ProcesoLLM
from aiaudit.score import opportunity

RAIZ = Path(__file__).resolve().parents[1]


def _crear_demo(base: Path):
    spec = importlib.util.spec_from_file_location("crear_demo", RAIZ / "scripts" / "crear_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.crear(base)


@pytest.fixture(scope="module")
def cliente(tmp_path_factory) -> Cliente:
    data = tmp_path_factory.mktemp("data")
    _crear_demo(data / "demo")
    c = Cliente.desde_nombre("demo", data_dir=data, overrides={"llm": {"provider": "offline"}})
    ingerir(c)
    anonimizar(c)
    return c


def test_ingesta_cubre_todos_los_tipos(cliente):
    tipos = {f.tipo_fuente for f in leer_jsonl(cliente.fragmentos, Fragmento)}
    assert {"documento", "tabla", "correo", "chat", "imagen"} <= tipos
    inventario = json.loads((cliente.interim / "inventario.json").read_text())
    assert not [i for i in inventario if i["error"]]
    assert list(cliente.tablas_log.glob("*.csv")), "el CSV de compras debe detectarse como log de eventos"


def test_anonimizacion_sin_pii(cliente):
    texto = "\n".join(f.texto for f in leer_jsonl(cliente.fragmentos_anon, Fragmento))
    for pii in ["Pedro Soto", "Luis Rojas", "Maria", "constructora-demo.cl", "8765 4321"]:
        assert pii not in texto
    assert "<PERSONA_" in texto and "<EMAIL_" in texto


def test_regex_no_borra_montos():
    a = Anonimizador(usar_presidio=False)
    t = a.texto("Total 15000000 CLP y 1.250.000; RUT 12.345.678-5; telefono: 22 345 6789")
    assert "15000000" in t and "1.250.000" in t
    assert "<RUT_1>" in t and "<TELEFONO_1>" in t
    assert revertir(t, a.mapa).endswith("telefono: 22 345 6789")


def test_mining_detecta_retrabajo(cliente):
    process_mining.minar(cliente)
    m = next(iter(json.loads((cliente.processed / "mining.json").read_text()).values()))
    assert m["casos"] == 12 and m["casos_con_retrabajo_pct"] == 0.25


def test_pipeline_offline_genera_entregables(cliente):
    process_extract.extraer(cliente)
    process_mining.minar(cliente)
    consolidate.consolidar(cliente)
    r = opportunity.priorizar(cliente)
    assert r["validadas"] == 3
    generar(cliente)
    for f in ["01_diagnostico.docx", "02_ruta_integracion_ia.docx", "03_plan_capacitacion.docx", "01_diagnostico.md"]:
        assert (cliente.outputs / f).stat().st_size > 1000
    diag = json.loads(cliente.diagnostico.read_text())
    compras = next(p for p in diag["procesos"] if p["id"] == "compras_materiales")
    assert set(compras["fuentes_deteccion"]) >= {"mining", "cuestionario"}


class FakeLLM:
    """Simula LLM.estructurado: devuelve una instancia valida del esquema pedido."""
    tokens_in = tokens_out = 0
    costo_usd = 0.0

    def __init__(self):
        self.llamadas = []

    def estructurado(self, sistema, usuario, esquema):
        self.llamadas.append(usuario)
        if esquema is ExtraccionLLM:
            return ExtraccionLLM(procesos=[ProcesoLLM(
                id_taxonomia="informes_obra", nombre="Informe semanal de obra", area="Obra", descripcion="Se arma los viernes",
                actores=["jefe de obra"], herramientas=["Word", "WhatsApp"],
                pasos=[PasoLLM(orden=1, descripcion="Copiar fotos de WhatsApp", actor="jefe de obra", herramienta="WhatsApp", manual=True),
                       PasoLLM(orden=2, descripcion="Redactar informe", actor="jefe de obra", herramienta="Word", manual=True)],
                frecuencia="semanal", dolores=["4 horas cada viernes"], evidencias=["x#0"], confianza=0.9)])
        if esquema is opportunity.PropuestaLLM:
            return opportunity.PropuestaLLM(solucion="Asistente que redacta el informe", tipo_solucion="generacion_documentos",
                                            herramientas_sugeridas=["OpenAI API"], automatizable=0.6, complejidad=2,
                                            justificacion="test")
        raise AssertionError(esquema)


def test_camino_llm_con_fake(cliente):
    llm = FakeLLM()
    frags = leer_jsonl(cliente.fragmentos_anon, Fragmento)
    procesos = process_extract.extraer_llm(frags, "construccion", cliente.settings["llm"], llm)
    assert procesos[0].id_taxonomia == "informes_obra" and llm.llamadas
    assert all("Pedro Soto" not in u for u in llm.llamadas), "al LLM solo debe llegar texto anonimizado"
    p = consolidate.fusionar([("llm", procesos[0])])[0]
    assert "flowchart LR" in consolidate.mermaid(p)
    o = opportunity.evaluar(p, opportunity._propuesta_llm(p, llm, []), cliente.settings["priorizacion"])
    assert o.fase.startswith("Fase 1") and not o.datos_validados


def test_entregables_con_ortografia(cliente):
    from docx import Document
    if not (cliente.outputs / "01_diagnostico.docx").exists():
        test_pipeline_offline_genera_entregables(cliente)
    titulos = {f: Document(cliente.outputs / f).paragraphs[0].text
               for f in ["01_diagnostico.docx", "02_ruta_integracion_ia.docx", "03_plan_capacitacion.docx"]}
    assert titulos == {"01_diagnostico.docx": "Diagnóstico de procesos e IA",
                       "02_ruta_integracion_ia.docx": "Ruta de integración de IA",
                       "03_plan_capacitacion.docx": "Plan de capacitación en IA"}


def test_offline_ignora_tildes():
    frags = [Fragmento(id=f"x#{i}", origen="x", tipo_fuente="documento", texto=t) for i, t in
             enumerate(["La CUBICACIÓN del edificio y el presupuesto", "Licitación: cubicación pendiente"])]
    procesos = process_extract.extraer_offline(frags, "construccion")
    assert "presupuestos" in {p.id_taxonomia for p in procesos}
