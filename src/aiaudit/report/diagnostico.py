"""Informe de diagnostico: fuentes analizadas, procesos detectados, dolores y oportunidades. DOCX + Markdown (con mermaid)."""
from __future__ import annotations

from aiaudit.config import Cliente
from aiaudit.report.docx_utils import dinero, nuevo, tabla, vinetas
from aiaudit.schemas import Diagnostico


def _empresa(cliente: Cliente) -> str:
    return (cliente.cuestionario.get("empresa") or {}).get("nombre") or cliente.nombre


def generar_docx(cliente: Cliente, diag: Diagnostico, moneda: str):
    doc = nuevo("Diagnostico de procesos e IA", _empresa(cliente))
    ops = diag.oportunidades
    total_h = sum(o.horas_ahorradas_mes for o in ops)
    total = sum(o.ahorro_mensual for o in ops)
    validadas = sum(o.datos_validados for o in ops)

    doc.add_heading("1. Resumen ejecutivo", 1)
    doc.add_paragraph(
        f"Se identificaron {len(diag.procesos)} procesos de trabajo y {len(ops)} oportunidades de integracion de IA. "
        f"El ahorro potencial estimado es de {total_h:.0f} horas/mes ({dinero(total, moneda)}/mes). "
        f"{validadas} de {len(ops)} estimaciones usan horas declaradas por el cliente; el resto son supuestos a validar.")
    if ops:
        doc.add_paragraph("Prioridades principales:")
        vinetas(doc, [f"{o.proceso_nombre}: {o.solucion} ({o.horas_ahorradas_mes:.0f} h/mes, {o.fase})" for o in ops[:3]])

    doc.add_heading("2. Fuentes analizadas", 1)
    tabla(doc, ["Tipo de archivo", "Cantidad"], [[k, v] for k, v in sorted(diag.resumen_fuentes.items())])

    doc.add_heading("3. Mapa de procesos", 1)
    tabla(doc, ["Proceso", "Area", "Confianza", "Fuentes", "Horas/mes"],
          [[p.nombre, p.area, f"{p.confianza:.0%}", ", ".join(p.fuentes_deteccion), p.horas_mes or "s/d"] for p in diag.procesos])

    for i, p in enumerate(diag.procesos, 1):
        doc.add_heading(f"3.{i} {p.nombre}", 2)
        doc.add_paragraph(p.descripcion)
        doc.add_paragraph(f"Actores: {', '.join(p.actores) or 's/d'} | Herramientas: {', '.join(p.herramientas) or 's/d'} | "
                          f"Frecuencia: {p.frecuencia}")
        if p.pasos:
            tabla(doc, ["#", "Paso", "Actor", "Herramienta", "Tipo"],
                  [[x.orden, x.descripcion, x.actor, x.herramienta, "Manual" if x.manual else "Automatico"]
                   for x in sorted(p.pasos, key=lambda x: x.orden)])
        doc.add_paragraph("Dolores detectados:")
        vinetas(doc, p.dolores)

    doc.add_heading("4. Oportunidades priorizadas", 1)
    tabla(doc, ["#", "Proceso", "Solucion", "h/mes ahorro", f"Ahorro/mes ({moneda})", "Complejidad", "Validado"],
          [[i, o.proceso_nombre, o.solucion, o.horas_ahorradas_mes, f"{o.ahorro_mensual:,.0f}", o.complejidad,
            "Si" if o.datos_validados else "No"] for i, o in enumerate(ops, 1)])

    doc.add_heading("5. Supuestos y limitaciones", 1)
    vinetas(doc, sorted({s for o in ops for s in o.supuestos}) + [
        "La deteccion se basa en los archivos entregados; procesos sin registro escrito pueden no aparecer.",
        "Las estimaciones de ahorro deben validarse con una medicion antes/despues en el piloto."])
    return doc


def generar_markdown(cliente: Cliente, diag: Diagnostico) -> str:
    out = [f"# Diagnostico de procesos e IA - {_empresa(cliente)}\n"]
    for p in diag.procesos:
        out.append(f"## {p.nombre}\n\n{p.descripcion}\n")
        if p.diagrama_mermaid:
            out.append(f"```mermaid\n{p.diagrama_mermaid}\n```\n")
        out += [f"- {d}" for d in p.dolores]
        out.append("")
    out.append("## Oportunidades\n\n| # | Proceso | Solucion | h/mes | Fase |\n|---|---|---|---|---|")
    out += [f"| {i} | {o.proceso_nombre} | {o.solucion} | {o.horas_ahorradas_mes} | {o.fase} |"
            for i, o in enumerate(diag.oportunidades, 1)]
    return "\n".join(out) + "\n"
