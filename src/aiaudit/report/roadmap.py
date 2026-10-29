"""Ruta de integracion de IA por fases + ficha tecnica de desarrollo por oportunidad."""
from __future__ import annotations

from itertools import groupby

from aiaudit.report.docx_utils import dinero, nuevo, tabla, vinetas
from aiaudit.schemas import Diagnostico

# Arquitectura de referencia por tipo de solucion: (flujo, entregables, riesgos)
ARQUITECTURA = {
    "extraccion_documental": (
        "Archivo (PDF/foto/planilla) -> conversión (Docling/OCR) -> LLM con salida estructurada (JSON) -> validación humana -> planilla/ERP",
        ["Script o flujo n8n de extracción", "Esquema de datos validado", "Tablero de revisión de errores"],
        ["Documentos de baja calidad o manuscritos", "Errores de extracción sin revisión humana"]),
    "generacion_documentos": (
        "Datos de entrada (notas, audio, formulario) -> LLM con plantilla y ejemplos de la empresa -> borrador DOCX/PDF -> revisión y envío",
        ["Plantillas con el formato de la empresa", "Prompt validado con ejemplos reales", "Flujo de generación"],
        ["Contenido incorrecto si no hay revisión", "Plantillas desactualizadas"]),
    "asistente_rag": (
        "Documentos de la empresa -> indexación (embeddings) -> asistente que responde citando la fuente -> derivación a humano si no sabe",
        ["Base de conocimiento indexada", "Asistente (web o chat)", "Procedimiento de actualización de documentos"],
        ["Respuestas inventadas si la base está incompleta", "Documentos confidenciales mal clasificados"]),
    "automatizacion_datos": (
        "Fuentes (Excel/ERP/correo) -> integración automática (n8n/Python) -> consolidación -> reporte o alerta generada por LLM",
        ["Conectores a las fuentes", "Flujo programado", "Reporte/alerta automática"],
        ["Cambios de formato en las fuentes", "Credenciales y permisos de acceso"]),
    "clasificacion": (
        "Entrada (formulario, correo, foto) -> LLM clasifica y extrae campos -> registro centralizado -> seguimiento",
        ["Formulario de captura", "Clasificador con categorías de la empresa", "Registro y tablero"],
        ["Categorías ambiguas", "Baja adopción del formulario en terreno"]),
}


def generar_docx(empresa: str, diag: Diagnostico, moneda: str):
    doc = nuevo("Ruta de integración de IA", empresa)
    ops = sorted(diag.oportunidades, key=lambda o: (o.fase, -o.puntaje))

    doc.add_heading("1. Visión general", 1)
    doc.add_paragraph("La ruta prioriza victorias rápidas para generar adopción y medir resultados antes de invertir en "
                      "integraciones y desarrollos a medida. Cada fase termina con una medición de horas reales ahorradas.")
    tabla(doc, ["Fase", "Iniciativas", "Horas/mes", f"Ahorro/mes ({moneda})"],
          [[fase, len(g := list(grupo)), f"{sum(o.horas_ahorradas_mes for o in g):.0f}",
            f"{sum(o.ahorro_mensual for o in g):,.0f}"] for fase, grupo in groupby(ops, key=lambda o: o.fase)])

    doc.add_heading("2. Cronograma", 1)
    for fase, grupo in groupby(ops, key=lambda o: o.fase):
        doc.add_heading(fase, 2)
        vinetas(doc, [f"{o.proceso_nombre}: {o.solucion}" for o in grupo])

    doc.add_heading("3. Fichas técnicas de desarrollo", 1)
    for i, o in enumerate(ops, 1):
        flujo, entregables, riesgos = ARQUITECTURA.get(o.tipo_solucion, ARQUITECTURA["automatizacion_datos"])
        doc.add_heading(f"3.{i} {o.proceso_nombre}", 2)
        tabla(doc, ["Campo", "Detalle"], [
            ["Objetivo", o.solucion],
            ["Área", o.area or "s/d"],
            ["Tipo de solución", o.tipo_solucion],
            ["Herramientas", ", ".join(o.herramientas_sugeridas)],
            ["Flujo propuesto", flujo],
            ["Complejidad", f"{o.complejidad}/5 - {o.fase}"],
            ["Impacto esperado", f"{o.horas_ahorradas_mes} h/mes ({o.automatizable:.0%} de {o.horas_mes} h) = "
                                 f"{dinero(o.ahorro_mensual, moneda)}/mes"],
            ["Datos validados", "Sí" if o.datos_validados else "No - " + "; ".join(o.supuestos)],
        ])
        doc.add_paragraph("Entregables:")
        vinetas(doc, entregables)
        doc.add_paragraph("Criterios de aceptación:")
        vinetas(doc, [f"Reducción medida de al menos {o.horas_ahorradas_mes * 0.7:.0f} h/mes en el piloto (70% de lo estimado)",
                      "Tasa de error igual o menor al proceso manual", "Usuarios capacitados operan sin soporte tras 2 semanas"])
        doc.add_paragraph("Riesgos:")
        vinetas(doc, riesgos)

    doc.add_heading("4. Gobierno y métricas", 1)
    vinetas(doc, ["Línea base: medir horas actuales de cada proceso antes del piloto.",
                  "KPIs: horas ahorradas, tiempo de ciclo, tasa de error, adopción (usuarios activos/semana).",
                  "Política de datos: qué información puede enviarse a proveedores de IA y cuál no.",
                  "Responsable interno por iniciativa (dueño del proceso)."])
    return doc
