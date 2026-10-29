"""Plan de capacitacion personalizado: un modulo por oportunidad, adaptado a roles y nivel digital del cliente.

Con proveedor LLM se generan contenidos a medida del proceso; en modo offline se usa una plantilla.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel

from aiaudit.report.docx_utils import nuevo, tabla, vinetas
from aiaudit.schemas import Diagnostico, Oportunidad, Proceso

log = logging.getLogger(__name__)

HORAS_POR_NIVEL = {"bajo": 4, "medio": 2.5, "alto": 1.5}


class ModuloLLM(BaseModel):
    objetivo: str
    conceptos: list[str]
    paso_a_paso: list[str]
    buenas_practicas: list[str]
    limites_y_riesgos: list[str]
    ejercicio_practico: str
    evaluacion: list[str]


SISTEMA = """Eres formador en adopcion de IA para equipos no tecnicos. Disenas un modulo de capacitacion practico
para que el equipo use la solucion implementada en SU proceso real. Lenguaje simple, sin jerga, orientado a la accion.
El ejercicio practico debe usar un caso del propio proceso. Responde en espanol."""


def modulo_base(o: Oportunidad, p: Proceso | None) -> ModuloLLM:
    return ModuloLLM(
        objetivo=f"Operar la solucion '{o.solucion}' en el proceso {o.proceso_nombre}.",
        conceptos=["Que hace la IA en este proceso y que sigue haciendo la persona",
                   "Entradas que necesita la herramienta y salidas que entrega"],
        paso_a_paso=["Preparar la informacion de entrada", f"Ejecutar el flujo en {o.herramientas_sugeridas[0]}",
                     "Revisar el resultado contra la fuente", "Corregir, aprobar y registrar"],
        buenas_practicas=["Revisar siempre antes de enviar o registrar", "Reportar errores repetidos al responsable",
                          "No ingresar datos personales fuera de la herramienta aprobada"],
        limites_y_riesgos=["La IA puede equivocarse con datos ambiguos o ilegibles", "No reemplaza la decision tecnica final"],
        ejercicio_practico=f"Procesar un caso real reciente de '{o.proceso_nombre}' con la herramienta y comparar con el resultado manual."
                           + (f" Pasos actuales de referencia: {', '.join(x.descripcion for x in p.pasos[:4])}." if p and p.pasos else ""),
        evaluacion=["El participante completa el ejercicio sin ayuda", "Identifica un error intencional en la salida de la IA"],
    )


def generar_docx(empresa: str, diag: Diagnostico, roles: list[dict], llm=None):
    doc = nuevo("Plan de capacitacion en IA", empresa)
    roles = [r for r in roles if r.get("rol")] or [{"rol": "Equipo general", "personas": None, "nivel_digital": "medio"}]
    procesos = {p.id: p for p in diag.procesos}

    doc.add_heading("1. Participantes", 1)
    tabla(doc, ["Rol", "Personas", "Nivel digital", "Horas por modulo"],
          [[r["rol"], r.get("personas") or "s/d", r.get("nivel_digital", "medio"),
            HORAS_POR_NIVEL.get(r.get("nivel_digital", "medio"), 2.5)] for r in roles])

    doc.add_heading("2. Modulo 0 - Fundamentos (todos los roles)", 1)
    vinetas(doc, ["Que es un modelo de lenguaje y que no es", "Como escribir instrucciones claras (prompts) con contexto, formato y ejemplos",
                  "Verificacion: por que siempre se revisa la salida", "Politica de datos de la empresa: que se puede y que no se puede compartir"])

    doc.add_heading("3. Modulos por proceso", 1)
    for i, o in enumerate(diag.oportunidades, 1):
        p = procesos.get(o.proceso_id)
        m = modulo_base(o, p)
        if llm:
            try:
                m = llm.estructurado(SISTEMA, f"Solucion: {o.solucion}\nHerramientas: {', '.join(o.herramientas_sugeridas)}\n"
                                              f"Proceso: {o.proceso_nombre}\nActores: {', '.join(p.actores) if p else ''}\n"
                                              f"Pasos actuales: {'; '.join(x.descripcion for x in p.pasos) if p else ''}\n"
                                              f"Roles a capacitar: {', '.join(r['rol'] for r in roles)}", ModuloLLM)
            except Exception as e:
                log.warning("Modulo LLM fallo para %s: %s (se usa plantilla)", o.proceso_id, e)
        doc.add_heading(f"Modulo {i} - {o.proceso_nombre}", 2)
        doc.add_paragraph(f"Objetivo: {m.objetivo}")
        doc.add_paragraph(f"Dirigido a: {', '.join(p.actores) if p and p.actores else 'responsables del proceso'} | {o.fase}")
        for titulo, items in [("Conceptos", m.conceptos), ("Paso a paso", m.paso_a_paso),
                              ("Buenas practicas", m.buenas_practicas), ("Limites y riesgos", m.limites_y_riesgos),
                              ("Evaluacion", m.evaluacion)]:
            doc.add_paragraph(titulo + ":").runs[0].bold = True
            vinetas(doc, items)
        doc.add_paragraph("Ejercicio practico:").runs[0].bold = True
        doc.add_paragraph(m.ejercicio_practico)

    doc.add_heading("4. Seguimiento post-capacitacion", 1)
    vinetas(doc, ["Semana 1-2: acompanamiento en uso real y registro de dudas", "Semana 4: medicion de adopcion y horas ahorradas",
                  "Mes 2: sesion de refuerzo y ajustes de prompts/plantillas"])
    return doc
