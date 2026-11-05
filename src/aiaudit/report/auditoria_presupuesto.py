"""Informe de auditoria de presupuestos: indicadores, partidas criticas, composicion del costo, hallazgos con su
ubicacion exacta y plan de mejora con la herramienta concreta para cada punto."""
from __future__ import annotations

from aiaudit.config import CONFIG_DIR, leer_yaml
from aiaudit.report.docx_utils import dinero, nuevo, tabla, vinetas

ETIQUETAS_SEVERIDAD = {"alta": "Alta", "media": "Media", "baja": "Baja"}


def _pct(v) -> str:
    return f"{v:.1%}".replace(".", ",") if v is not None else "s/d"


def _ubicacion(h: dict) -> str:
    """'Hoja: A1, B2'; si las celdas ya traen la hoja ('Hoja!A1') no se repite."""
    celdas = ", ".join(h["celdas"][:6]) + ("..." if len(h["celdas"]) > 6 else "")
    if not celdas:
        return h["hoja"]
    return celdas if "!" in h["celdas"][0] else f"{h['hoja']}: {celdas}"


def _herramienta(h: dict) -> str:
    estado = f"verificado {h['verificado']}" if h.get("verificado") else "verificar antes de proponer"
    return f"{h['nombre']} ({estado})"


def generar_docx(empresa: str, analisis: list[dict], moneda: str):
    doc = nuevo("Auditoría de presupuestos", empresa)
    recomendaciones = leer_yaml(CONFIG_DIR / "recomendaciones_presupuesto.yaml")
    herramientas = leer_yaml(CONFIG_DIR / "herramientas.yaml")
    usadas: list[str] = []

    for n, a in enumerate(analisis, 1):
        m, hallazgos = a["metricas"], a["hallazgos"]
        doc.add_heading(f"{n}. {a['archivo']}", 1)

        doc.add_heading("Indicadores", 2)
        margen = (m["total"] - m["costo_directo"]) / m["total"] if m.get("total") else None
        tabla(doc, ["Indicador", "Valor"], [
            ["Costo directo", dinero(m["costo_directo"], moneda)],
            ["Gastos generales", dinero(m["gastos_generales"], moneda) if m.get("gastos_generales") else "s/d"],
            ["Utilidad", dinero(m["utilidad"], moneda) if m.get("utilidad") else "s/d"],
            ["Total del presupuesto", dinero(m["total"], moneda) if m.get("total") else "s/d"],
            ["Costo de construcción por m²", dinero(m["costo_m2"], moneda) if m.get("costo_m2") else "s/d"],
            ["Precio de venta por m²", dinero(m["venta_m2"], moneda) if m.get("venta_m2") else "s/d"],
            ["Diferencia total vs. costo directo", _pct(margen)],
            ["Partidas / con APU vinculado", f"{m['partidas']} / {m['partidas_con_apu']}"],
        ])

        doc.add_heading("Cómo está construido el cálculo", 2)
        mod = a["modelo"]
        roles = ", ".join(f"{rol}: «{hoja}»" for rol, hoja in a["hojas"].items())
        doc.add_paragraph(
            f"Hojas reconocidas: {roles}. El libro tiene {mod['formulas']} fórmulas, {mod['referencias_entre_hojas']} de ellas "
            f"conectan hojas distintas, y la cadena de cálculo más larga tiene {mod['profundidad_calculo']} pasos "
            f"(de un insumo hasta el total). {mod['apu']} análisis de precio unitario (APU) detectados."
            + (" Los valores se recalcularon porque el archivo no los traía." if a["recalculado"] else ""))

        doc.add_heading("Partidas críticas (Pareto)", 2)
        criticas = sorted([p for p in a["partidas"] if p["clase"] == "A"], key=lambda p: -(p["total"] or 0))
        doc.add_paragraph(f"{len(criticas)} de {len(a['partidas'])} partidas concentran el 80% del costo directo. "
                          "Cualquier error de cantidad o precio en ellas mueve el resultado del proyecto; "
                          "son las primeras que hay que respaldar y revisar.")
        tabla(doc, ["Ítem", "Partida", "Unidad", "Total", "% del CD", "Respaldo"],
              [[p["item"], p["partida"], p["unidad"], dinero(p["total"] or 0, moneda), _pct(p["participacion"]),
                "APU" if p["apu"] else ("Precio fijo" if not p["pu_formula"] else "Fórmula")] for p in criticas])

        doc.add_heading("Distribución por capítulo", 2)
        tabla(doc, ["Capítulo", "Total", "% del CD"],
              [[c["capitulo"], dinero(c["total"], moneda), _pct(c["participacion"])] for c in a["capitulos"]])

        comp = a["composicion"]
        total_comp = sum(comp.values())
        if total_comp:
            doc.add_heading("Composición del costo", 2)
            tabla(doc, ["Componente", "Monto", "%"], [
                [nombre, dinero(comp[k], moneda), _pct(comp[k] / total_comp)]
                for k, nombre in [("materiales", "Materiales"), ("mano_obra", "Mano de obra"), ("equipos", "Equipos"),
                                  ("sin_apu", "Sin desglose (sin APU)")]])
            if comp["sin_apu"] / total_comp > 0.25:
                doc.add_paragraph(f"El {_pct(comp['sin_apu'] / total_comp)} del costo no tiene desglose: no se sabe "
                                  "cuánto es material, mano de obra o subcontrato.")

        doc.add_heading("Hallazgos", 2)
        tabla(doc, ["Severidad", "Hallazgo", "Ubicación", "Impacto"],
              [[ETIQUETAS_SEVERIDAD[h["severidad"]], h["descripcion"],
                _ubicacion(h), h["impacto"]] for h in hallazgos])

        doc.add_heading("Plan de mejora", 2)
        tipos = sorted({h["tipo"] for h in hallazgos}, key=lambda t: (recomendaciones.get(t, {}).get("prioridad", 9), t))
        filas = []
        for t in tipos:
            r = recomendaciones.get(t)
            if not r:
                continue
            herrs = [_herramienta(herramientas[i]) for i in r["herramientas"] if i in herramientas]
            usadas += [i for i in r["herramientas"] if i in herramientas and i not in usadas]
            filas.append([r["prioridad"], r["criterio"], r["mejora"], r["ia"], "; ".join(herrs) or "-"])
        tabla(doc, ["Prioridad", "Criterio", "Qué mejorar", "Dónde entra la IA", "Herramienta"], filas)

    doc.add_heading(f"{len(analisis) + 1}. Herramientas recomendadas", 1)
    for i in usadas:
        h = herramientas[i]
        doc.add_heading(h["nombre"], 3)
        vinetas(doc, [f"Para qué: {h['uso']}", f"Requisitos: {h['requisitos']}", f"Cómo instalar: {h['instalar']}",
                      f"Limitaciones: {h['limitaciones']}",
                      f"Fuente: {h['fuente']} ({'verificado ' + str(h['verificado']) if h.get('verificado') else 'verificar antes de proponer'})"])
    return doc
