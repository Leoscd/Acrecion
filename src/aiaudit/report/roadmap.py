"""Ruta de integracion de IA por fases + ficha tecnica de desarrollo por oportunidad."""
from __future__ import annotations

from itertools import groupby

from aiaudit.report.docx_utils import dinero, nuevo, tabla, vinetas
from aiaudit.schemas import Diagnostico

# Arquitectura de referencia por tipo de solucion: (flujo, entregables, riesgos)
ARQUITECTURA = {
    "extraccion_documental": (
        "Archivo (PDF/foto/planilla) -> conversion (Docling/OCR) -> LLM con salida estructurada (JSON) -> validacion humana -> planilla/ERP",
        ["Script o flujo n8n de extraccion", "Esquema de datos validado", "Tablero de revision de errores"],
        ["Documentos de baja calidad o manuscritos", "Errores de extraccion sin revision humana"]),
    "generacion_documentos": (
        "Datos de entrada (notas, audio, formulario) -> LLM con plantilla y ejemplos de la empresa -> borrador DOCX/PDF -> revision y envio",
        ["Plantillas con el formato de la empresa", "Prompt validado con ejemplos reales", "Flujo de generacion"],
        ["Contenido incorrecto si no hay revision", "Plantillas desactualizadas"]),
    "asistente_rag": (
        "Documentos de la empresa -> indexacion (embeddings) -> asistente que responde citando la fuente -> derivacion a humano si no sabe",
        ["Base de conocimiento indexada", "Asistente (web o chat)", "Procedimiento de actualizacion de documentos"],
        ["Respuestas inventadas si la base esta incompleta", "Documentos confidenciales mal clasificados"]),
    "automatizacion_datos": (
        "Fuentes (Excel/ERP/correo) -> integracion automatica (n8n/Python) -> consolidacion -> reporte o alerta generada por LLM",
        ["Conectores a las fuentes", "Flujo programado", "Reporte/alerta automatica"],
        ["Cambios de formato en las fuentes", "Credenciales y permisos de acceso"]),
    "clasificacion": (
        "Entrada (formulario, correo, foto) -> LLM clasifica y extrae campos -> registro centralizado -> seguimiento",
        ["Formulario de captura", "Clasificador con categorias de la empresa", "Registro y tablero"],
        ["Categorias ambiguas", "Baja adopcion del formulario en terreno"]),
}


def generar_docx(empresa: str, diag: Diagnostico, moneda: str):
    doc = nuevo("Ruta de integracion de IA", empresa)
    ops = sorted(diag.oportunidades, key=lambda o: (o.fase, -o.puntaje))

    doc.add_heading("1. Vision general", 1)
    doc.add_paragraph("La ruta prioriza victorias rapidas para generar adopcion y medir resultados antes de invertir en "
                      "integraciones y desarrollos a medida. Cada fase termina con una medicion de horas reales ahorradas.")
    tabla(doc, ["Fase", "Iniciativas", "Horas/mes", f"Ahorro/mes ({moneda})"],
          [[fase, len(g := list(grupo)), f"{sum(o.horas_ahorradas_mes for o in g):.0f}",
            f"{sum(o.ahorro_mensual for o in g):,.0f}"] for fase, grupo in groupby(ops, key=lambda o: o.fase)])

    doc.add_heading("2. Cronograma", 1)
    for fase, grupo in groupby(ops, key=lambda o: o.fase):
        doc.add_heading(fase, 2)
        vinetas(doc, [f"{o.proceso_nombre}: {o.solucion}" for o in grupo])

    doc.add_heading("3. Fichas tecnicas de desarrollo", 1)
    for i, o in enumerate(ops, 1):
        flujo, entregables, riesgos = ARQUITECTURA.get(o.tipo_solucion, ARQUITECTURA["automatizacion_datos"])
        doc.add_heading(f"3.{i} {o.proceso_nombre}", 2)
        tabla(doc, ["Campo", "Detalle"], [
            ["Objetivo", o.solucion],
            ["Area", o.area or "s/d"],
            ["Tipo de solucion", o.tipo_solucion],
            ["Herramientas", ", ".join(o.herramientas_sugeridas)],
            ["Flujo propuesto", flujo],
            ["Complejidad", f"{o.complejidad}/5 - {o.fase}"],
            ["Impacto esperado", f"{o.horas_ahorradas_mes} h/mes ({o.automatizable:.0%} de {o.horas_mes} h) = "
                                 f"{dinero(o.ahorro_mensual, moneda)}/mes"],
            ["Datos validados", "Si" if o.datos_validados else "No - " + "; ".join(o.supuestos)],
        ])
        doc.add_paragraph("Entregables:")
        vinetas(doc, entregables)
        doc.add_paragraph("Criterios de aceptacion:")
        vinetas(doc, [f"Reduccion medida de al menos {o.horas_ahorradas_mes * 0.7:.0f} h/mes en el piloto (70% de lo estimado)",
                      "Tasa de error igual o menor al proceso manual", "Usuarios capacitados operan sin soporte tras 2 semanas"])
        doc.add_paragraph("Riesgos:")
        vinetas(doc, riesgos)

    doc.add_heading("4. Gobierno y metricas", 1)
    vinetas(doc, ["Linea base: medir horas actuales de cada proceso antes del piloto.",
                  "KPIs: horas ahorradas, tiempo de ciclo, tasa de error, adopcion (usuarios activos/semana).",
                  "Politica de datos: que informacion puede enviarse a proveedores de IA y cual no.",
                  "Responsable interno por iniciativa (dueno del proceso)."])
    return doc
