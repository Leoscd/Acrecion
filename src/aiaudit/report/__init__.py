"""Genera los entregables en outputs/: diagnostico, roadmap y capacitacion (DOCX) + diagnostico.md."""
from __future__ import annotations

import json

from aiaudit.config import Cliente
from aiaudit.schemas import Diagnostico


def generar(cliente: Cliente) -> dict:
    from aiaudit.report import capacitacion, diagnostico, roadmap

    s = cliente.settings
    moneda = s["priorizacion"]["moneda"]
    diag = Diagnostico.model_validate_json(cliente.diagnostico.read_text(encoding="utf-8"))
    q = cliente.cuestionario
    empresa = (q.get("empresa") or {}).get("nombre") or cliente.nombre
    llm = None
    if s["llm"]["provider"] != "offline":
        from aiaudit.llm.client import LLM
        llm = LLM(s["llm"], cliente.interim / "llm_log.jsonl")

    salidas = {
        "01_diagnostico.docx": diagnostico.generar_docx(cliente, diag, moneda),
        "02_ruta_integracion_ia.docx": roadmap.generar_docx(empresa, diag, moneda),
        "03_plan_capacitacion.docx": capacitacion.generar_docx(empresa, diag, q.get("roles") or [], llm),
    }
    presupuestos = cliente.processed / "presupuestos.json"
    analisis = json.loads(presupuestos.read_text(encoding="utf-8")) if presupuestos.exists() else []
    if analisis:
        from aiaudit.report import auditoria_presupuesto
        moneda_pres = (s.get("construccion") or {}).get("moneda_presupuesto", "$")
        salidas["04_auditoria_presupuestos.docx"] = auditoria_presupuesto.generar_docx(empresa, analisis, moneda_pres)
    for nombre, doc in salidas.items():
        doc.save(cliente.outputs / nombre)
    (cliente.outputs / "01_diagnostico.md").write_text(diagnostico.generar_markdown(cliente, diag), encoding="utf-8")
    return {"archivos": sorted([*salidas, "01_diagnostico.md"])}
