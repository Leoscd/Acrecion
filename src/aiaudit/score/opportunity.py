"""Priorizacion de oportunidades de IA por proceso.

ahorro_mensual = horas_mes * automatizable * costo_hora
puntaje        = ahorro_mensual * factor_dolor * confianza / complejidad
Si las horas no vienen del cuestionario se usa un supuesto y la oportunidad queda como NO validada.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel

from aiaudit.config import Cliente, taxonomia
from aiaudit.schemas import Diagnostico, Oportunidad, Proceso

log = logging.getLogger(__name__)

HERRAMIENTAS = {
    "extraccion_documental": ["OpenAI API (vision + structured outputs)", "Docling", "n8n"],
    "generacion_documentos": ["OpenAI API / ChatGPT con plantillas", "docxtpl", "n8n"],
    "asistente_rag": ["OpenAI File Search", "LlamaIndex", "Open WebUI"],
    "automatizacion_datos": ["n8n", "Python + pandas", "API de Google Sheets / Excel"],
    "clasificacion": ["OpenAI API (structured outputs)", "Google Forms / AppSheet", "n8n"],
}
FASES = {1: "Fase 1 - Quick wins (0-1 mes)", 2: "Fase 1 - Quick wins (0-1 mes)",
         3: "Fase 2 - Integraciones (1-3 meses)", 4: "Fase 3 - Sistemas a medida (3-6 meses)",
         5: "Fase 3 - Sistemas a medida (3-6 meses)"}


class PropuestaLLM(BaseModel):
    solucion: str
    tipo_solucion: str              # una de las claves de HERRAMIENTAS
    herramientas_sugeridas: list[str]
    automatizable: float            # 0-1
    complejidad: int                # 1-5
    justificacion: str


SISTEMA = f"""Eres arquitecto de soluciones de IA para PyMEs y constructoras. Para el proceso descrito propone UNA solucion
concreta y realista de implementar con herramientas actuales (APIs de LLM, automatizacion no-code, scripts Python).
- tipo_solucion: una de {list(HERRAMIENTAS)}.
- automatizable: fraccion de las horas del proceso que la solucion elimina (se conservador: 0.2-0.7 habitual).
- complejidad: 1 prompt/plantilla, 2 no-code, 3 integracion entre sistemas, 4 desarrollo a medida, 5 proyecto con datos/modelos propios.
- Considera las herramientas que la empresa ya usa. Responde en espanol."""


def _propuesta_base(p: Proceso, tax: dict) -> PropuestaLLM:
    if t := tax.get(p.id):
        return PropuestaLLM(solucion=t["solucion_ia"], tipo_solucion=t["tipo_solucion"],
                            herramientas_sugeridas=HERRAMIENTAS[t["tipo_solucion"]], automatizable=t["automatizable"],
                            complejidad=t["complejidad"], justificacion="Segun catalogo del sector.")
    manuales = sum(x.manual for x in p.pasos)
    return PropuestaLLM(solucion="Revisar en taller con el cliente: proceso fuera del catalogo.", tipo_solucion="automatizacion_datos",
                        herramientas_sugeridas=HERRAMIENTAS["automatizacion_datos"],
                        automatizable=0.3 if manuales else 0.2, complejidad=3, justificacion="Valores por defecto.")


def _propuesta_llm(p: Proceso, llm, herramientas_cliente: list[str]) -> PropuestaLLM:
    pasos = "\n".join(f"{x.orden}. {x.descripcion} [{x.actor}; {x.herramienta}; {'manual' if x.manual else 'auto'}]" for x in p.pasos)
    usuario = (f"Proceso: {p.nombre} ({p.area})\nDescripcion: {p.descripcion}\nPasos:\n{pasos or '(sin detalle)'}\n"
               f"Herramientas actuales del proceso: {', '.join(p.herramientas)}\nDolores: {'; '.join(p.dolores)}\n"
               f"Herramientas de la empresa: {', '.join(herramientas_cliente) or 'no declaradas'}")
    return llm.estructurado(SISTEMA, usuario, PropuestaLLM)


def evaluar(p: Proceso, prop: PropuestaLLM, cfg: dict) -> Oportunidad:
    supuestos = []
    horas = p.horas_mes
    if horas is None:
        horas = cfg["horas_mes_supuesto"]
        supuestos.append(f"Horas/mes supuestas ({horas} h): validar con el cliente")
    costo = p.costo_hora if p.costo_hora is not None else cfg["costo_hora_default"]
    if p.costo_hora is None:
        supuestos.append(f"Costo hora por defecto ({costo} {cfg['moneda']})")
    auto = max(0.0, min(1.0, prop.automatizable))
    compl = max(1, min(5, prop.complejidad))
    ahorradas = horas * auto
    ahorro = ahorradas * costo
    factor_dolor = (p.dolor or 3) / 3
    return Oportunidad(
        proceso_id=p.id, proceso_nombre=p.nombre, area=p.area, solucion=prop.solucion,
        tipo_solucion=prop.tipo_solucion, herramientas_sugeridas=prop.herramientas_sugeridas,
        horas_mes=round(horas, 1), automatizable=round(auto, 2), horas_ahorradas_mes=round(ahorradas, 1),
        ahorro_mensual=round(ahorro, 2), complejidad=compl,
        puntaje=round(ahorro * factor_dolor * max(p.confianza, 0.1) / compl, 2),
        fase=FASES[compl], datos_validados=p.horas_mes is not None, supuestos=supuestos,
    )


def priorizar(cliente: Cliente) -> dict:
    s = cliente.settings
    cfg, cfg_llm = s["priorizacion"], s["llm"]
    diag = Diagnostico.model_validate_json(cliente.diagnostico.read_text(encoding="utf-8"))
    tax = {t["id"]: t for t in taxonomia(diag.sector)}
    llm = None
    if cfg.get("propuestas_llm") and cfg_llm["provider"] != "offline":
        from aiaudit.llm.client import LLM
        llm = LLM(cfg_llm, cliente.interim / "llm_log.jsonl")
    herramientas_cliente = (cliente.cuestionario.get("empresa") or {}).get("herramientas_actuales") or []

    oportunidades = []
    for p in diag.procesos:
        prop = _propuesta_base(p, tax)
        if llm:
            try:
                prop = _propuesta_llm(p, llm, herramientas_cliente)
            except Exception as e:
                log.warning("Propuesta LLM fallo para %s: %s (se usa catalogo)", p.id, e)
        oportunidades.append(evaluar(p, prop, cfg))
    diag.oportunidades = sorted(oportunidades, key=lambda o: o.puntaje, reverse=True)
    cliente.diagnostico.write_text(diag.model_dump_json(indent=2), encoding="utf-8")
    return {"oportunidades": len(oportunidades),
            "ahorro_mensual_total": round(sum(o.ahorro_mensual for o in oportunidades), 2),
            "horas_ahorradas_mes": round(sum(o.horas_ahorradas_mes for o in oportunidades), 1),
            "validadas": sum(o.datos_validados for o in oportunidades)}
