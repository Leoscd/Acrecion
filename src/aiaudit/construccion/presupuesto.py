"""Auditoria de presupuestos de obra en Excel.

Reconoce la estructura tipica (Parametros, Insumos, APU, Presupuesto/Itemizado, Resumen), reconstruye
partidas y precios unitarios, calcula indicadores (costo directo, total, costo/m2, venta/m2), detecta las
partidas criticas (Pareto ABC) y la composicion del costo (materiales, mano de obra, equipos), y lista
hallazgos con su ubicacion exacta para que cada uno se pueda corregir.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, Field

from aiaudit.construccion.excel_modelo import Celda, ModeloExcel, cargar, firma_relativa

log = logging.getLogger(__name__)

SEVERIDADES = ("alta", "media", "baja")
PIE = {"costo directo": "costo_directo_declarado", "gastos generales": "gastos_generales", "utilidad": "utilidad",
       "neto": "neto", "iva": "iva"}
NUMEROS_NEUTROS = {0.0, 1.0, 2.0, 10.0, 100.0, 1000.0}
UNIDADES_GLOBALES = {"gl", "glb", "global", "est", "estimado", "sg"}
MANO_OBRA = re.compile(r"\b(hh|hd|jornal|maestro|ayudante|jornalero|capataz|oficial|peon|mano de obra|m\.?o\.?)\b")
EQUIPOS = re.compile(r"\b(betonera|equipo|arriendo|grua|retroexcavadora|vibrador|andamio|camion|maquinaria)\b")


def norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "").lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%/. ]", " ", t)).strip()


def norm_unidad(u) -> str:
    u = norm(u).replace(" ", "").replace(".", "")
    return {"mt2": "m2", "mts2": "m2", "m²": "m2", "mt3": "m3", "mts3": "m3", "mt": "ml", "mts": "ml", "m": "ml",
            "und": "un", "u": "un", "unid": "un", "glb": "gl", "global": "gl"}.get(u, u)


# ---------- Modelos de salida ----------

class Hallazgo(BaseModel):
    tipo: str
    severidad: str
    hoja: str
    celdas: list[str] = Field(default_factory=list)
    descripcion: str
    impacto: str = ""


class Partida(BaseModel):
    item: str
    partida: str
    capitulo: str
    unidad: str
    cantidad: float
    precio_unitario: float | None
    total: float | None
    fila: int
    pu_formula: bool
    apu: str | None = None          # celda del APU de la que sale el PU
    clase: str = ""                 # A / B / C (Pareto)
    participacion: float = 0.0


class AnalisisPresupuesto(BaseModel):
    archivo: str
    hojas: dict[str, str]                       # rol -> nombre de hoja
    recalculado: bool
    metricas: dict
    modelo: dict                                # estructura del calculo
    partidas: list[Partida]
    capitulos: list[dict]
    composicion: dict                           # materiales / mano_obra / equipos (en $)
    hallazgos: list[Hallazgo]


# ---------- Reconocimiento de estructura ----------

def _texto(c: Celda | None) -> str:
    return norm(c.valor) if c and isinstance(c.valor, str) else ""


def _num(c: Celda | None) -> float | None:
    return float(c.valor) if c and isinstance(c.valor, (int, float)) and not isinstance(c.valor, bool) else None


def _filas(m: ModeloExcel, hoja: str) -> dict[int, dict[int, Celda]]:
    filas: dict[int, dict[int, Celda]] = defaultdict(dict)
    for c in m.de_hoja(hoja):
        filas[c.fila][c.col] = c
    return filas


def _encabezado_presupuesto(m: ModeloExcel, hoja: str) -> tuple[int, dict[str, int]] | None:
    """Busca la fila de titulos del itemizado y la columna de cada campo."""
    claves = {
        "item": ("item", "n ", "n.", "codigo", "cod"),
        "partida": ("partida", "descripcion", "designacion", "detalle"),
        "unidad": ("unidad", "ud", "un", "unid"),
        "cantidad": ("cantidad", "cant"),
        "pu": ("unitario", "p.u", "pu", "precio un"),
        "total": ("total", "subtotal", "parcial", "monto"),
    }
    for fila, cols in sorted(_filas(m, hoja).items())[:40]:
        encontrados = {}
        for col, c in cols.items():
            t = _texto(c)
            if not t:
                continue
            for campo, pals in claves.items():
                if campo in encontrados:
                    continue
                if campo == "total" and "unitario" in t:
                    continue
                # "P. Unitario", "Precio unitario": el precio unitario se reconoce por contener "unitario"
                if (campo == "pu" and "unitario" in t) or any(t == p.strip() or t.startswith(p) for p in pals):
                    encontrados[campo] = col
                    break
        if {"partida", "cantidad", "total"} <= encontrados.keys():
            return fila, encontrados
    return None


def _roles(m: ModeloExcel) -> dict[str, str]:
    roles = {}
    for hoja in m.hojas:
        if hoja in m.hojas_ocultas:
            continue
        n = norm(hoja)
        textos = " ".join(_texto(c) for c in m.de_hoja(hoja) if c.fila <= 30)
        if "presupuesto" not in roles and _encabezado_presupuesto(m, hoja):
            roles["presupuesto"] = hoja
        elif "apu" not in roles and ("apu" in n or "analisis" in n or "rendimiento" in textos):
            roles["apu"] = hoja
        elif "insumos" not in roles and ("insumo" in n or "material" in n or "precios" in n):
            roles["insumos"] = hoja
        elif "parametros" not in roles and ("parametro" in n or "datos" in n):
            roles["parametros"] = hoja
        elif "resumen" not in roles and ("resumen" in n or "m2" in textos):
            roles["resumen"] = hoja
    return roles


# ---------- Extraccion ----------

def _partidas(m: ModeloExcel, hoja: str) -> tuple[list[Partida], dict[str, tuple[int, float | None]]]:
    fila_enc, cols = _encabezado_presupuesto(m, hoja)
    partidas, pie, capitulo = [], {}, ""
    for fila, celdas in sorted(_filas(m, hoja).items()):
        if fila <= fila_enc:
            continue
        desc_c = celdas.get(cols["partida"])
        desc = str(desc_c.valor).strip() if desc_c and desc_c.valor is not None else ""
        cant = _num(celdas.get(cols["cantidad"]))
        total_c = celdas.get(cols["total"])
        if desc and cant is not None:
            pu_c = celdas.get(cols.get("pu", -1))
            apu = next((f"{r.hoja}!{r.rango}" for r in (pu_c.referencias if pu_c else []) if r.hoja != hoja), None)
            partidas.append(Partida(
                item=str(celdas[cols["item"]].valor) if cols.get("item") in celdas else "", partida=desc,
                capitulo=capitulo, unidad=str((celdas.get(cols.get("unidad", -1)) or Celda(hoja, "", 0, 0, None, "")).valor or ""),
                cantidad=cant, precio_unitario=_num(pu_c), total=_num(total_c), fila=fila,
                pu_formula=bool(pu_c and pu_c.formula), apu=apu))
        elif desc:
            etiqueta = norm(desc)
            if any(etiqueta.startswith(k) for k in ("costo directo", "gastos generales", "utilidad", "neto", "iva", "total")):
                pie[etiqueta] = (fila, _num(total_c))
            else:
                capitulo = desc
    return partidas, pie


def _bloques_apu(m: ModeloExcel, hoja: str) -> dict[str, dict]:
    """APU: bloques con lineas (insumo, unidad, rendimiento, precio, parcial) cerrados por una fila de total.
    Devuelve {celda_pu: {nombre, unidad, lineas: [...], fila_ini, fila_fin}}."""
    bloques, actual = {}, None
    filas = _filas(m, hoja)
    for fila in sorted(filas):
        celdas = filas[fila]
        textos = [norm(c.valor) for c in celdas.values() if isinstance(c.valor, str)]
        if any("rendimiento" in t for t in textos):
            titulo = filas.get(fila - 1, {})
            nombre = " ".join(str(c.valor) for _, c in sorted(titulo.items()) if isinstance(c.valor, str))
            actual = {"nombre": nombre, "lineas": [], "fila_ini": fila + 1, "cols": {
                norm(c.valor): col for col, c in celdas.items() if isinstance(c.valor, str)}}
            continue
        if actual is None:
            continue
        cols = actual["cols"]
        c_rend, c_parcial = cols.get("rendimiento"), cols.get("parcial") or cols.get("total")
        rend = _num(celdas.get(c_rend))
        if rend is not None:
            actual["lineas"].append({
                "insumo": str(celdas[cols["insumo"]].valor) if cols.get("insumo") in celdas else "",
                "unidad": celdas.get(cols.get("unidad", -1)), "rendimiento": rend,
                "precio": _num(celdas.get(cols.get("precio", -1))), "parcial": _num(celdas.get(c_parcial)),
                "celda_parcial": celdas.get(c_parcial), "fila": fila})
        elif c_parcial in celdas and celdas[c_parcial].formula and actual["lineas"]:
            pu = celdas[c_parcial]
            bloques[pu.id] = {**actual, "fila_fin": fila, "celda_pu": pu}
            actual = None
    return bloques


def _metricas_etiquetadas(m: ModeloExcel) -> dict[str, float]:
    """Busca en todas las hojas etiquetas conocidas con un numero a la derecha."""
    patrones = {
        "superficie_construida": r"superficie construida|sup\.? construida|m2 construidos",
        "superficie_vendible": r"superficie (vendible|util|de venta)|sup\.? (vendible|util)|m2 vendibles",
        "costo_m2": r"costo .*por m2|costo .*/m2|costo m2",
        "venta_m2": r"(precio|valor) de venta .*m2|venta .*/m2",
        "gg_pct": r"^gastos generales \(?%",
        "utilidad_pct": r"^utilidad \(?%",
    }
    salida = {}
    for hoja in m.hojas:
        filas = _filas(m, hoja)
        for fila, celdas in filas.items():
            for col, c in sorted(celdas.items()):
                t = _texto(c)
                if not t:
                    continue
                for clave, pat in patrones.items():
                    if clave in salida or not re.search(pat, t):
                        continue
                    valor = next((_num(v) for k, v in sorted(celdas.items()) if k > col and _num(v) is not None), None)
                    if valor is not None:
                        salida[clave] = valor
    return salida


# ---------- Hallazgos ----------

def _hallazgos(m: ModeloExcel, roles: dict, partidas: list[Partida], bloques: dict, pie: dict,
               metricas: dict) -> list[Hallazgo]:
    h: list[Hallazgo] = []
    pres = roles.get("presupuesto", "")

    # Errores de formula: se mide cuantas celdas contaminan aguas abajo usando el grafo
    por_hoja = defaultdict(list)
    for c in m.celdas.values():
        if c.es_error:
            por_hoja[c.hoja].append(c)
    for hoja, celdas in por_hoja.items():
        afectadas = set()
        for c in celdas:
            if c.id in m.grafo:
                afectadas |= nx.descendants(m.grafo, c.id)
        afectadas -= {c.id for c in celdas}
        externo = any(r.externa for c in celdas for r in c.referencias)
        h.append(Hallazgo(tipo="error_formula", severidad="alta" if afectadas else "media", hoja=hoja,
                          celdas=[c.coord for c in celdas][:15],
                          descripcion=f"{len(celdas)} celda(s) con error ({', '.join(sorted({str(c.valor) for c in celdas if c.valor}))})"
                                      + (" por un vínculo externo roto" if externo else "")
                                      + (f"; contaminan {len(afectadas)} celda(s) dependientes." if afectadas
                                         else "; por ahora no afectan otros cálculos."),
                          impacto="Los totales que dependen de estas celdas son incorrectos." if afectadas
                                  else "Error latente: si alguien vincula estas celdas, el error se propaga."))

    # Referencias circulares
    for ciclo in m.ciclos(3):
        h.append(Hallazgo(tipo="referencia_circular", severidad="alta", hoja=ciclo[0].split("!")[0], celdas=ciclo[:10],
                          descripcion="Referencia circular: el cálculo depende de sí mismo.",
                          impacto="Los valores cambian según el orden de cálculo; el total no es confiable."))

    # Vinculos a otros libros
    externas = [c.id for c in m.formulas if any(r.externa for r in c.referencias)]
    if externas or m.vinculos_externos:
        h.append(Hallazgo(tipo="vinculo_externo", severidad="media", hoja=externas[0].split("!")[0] if externas else "",
                          celdas=externas[:15],
                          descripcion=f"El libro depende de {max(m.vinculos_externos, 1)} archivo(s) externo(s).",
                          impacto="Si el archivo externo se mueve o cambia, los precios se rompen o quedan desactualizados sin aviso."))

    # Hojas ocultas
    for hoja in m.hojas_ocultas:
        h.append(Hallazgo(tipo="hoja_oculta", severidad="baja", hoja=hoja,
                          descripcion=f"Hoja oculta '{hoja}'.",
                          impacto="Datos que nadie revisa pueden contener precios antiguos o cálculos paralelos."))

    if pres:
        hay_apu = bool(bloques)
        # Precios unitarios escritos a mano
        sin_apu = [p for p in partidas if not p.pu_formula and p.precio_unitario is not None]
        no_gl = [p for p in sin_apu if norm_unidad(p.unidad) not in UNIDADES_GLOBALES]
        gl = [p for p in sin_apu if norm_unidad(p.unidad) in UNIDADES_GLOBALES]
        if no_gl:
            h.append(Hallazgo(tipo="pu_sin_apu", severidad="alta" if hay_apu else "media", hoja=pres,
                              celdas=[f"{p.item} {p.partida}" for p in no_gl],
                              descripcion=f"{len(no_gl)} partida(s) con precio unitario escrito a mano"
                                          + (" aunque el libro tiene APU." if hay_apu else ", sin análisis de precio."),
                              impacto="Si cambian los insumos, estos precios no se actualizan: el presupuesto queda desfasado."))
        if gl:
            h.append(Hallazgo(tipo="pu_global_sin_respaldo", severidad="media", hoja=pres,
                              celdas=[f"{p.item} {p.partida}" for p in gl],
                              descripcion=f"{len(gl)} partida(s) globales (gl) con monto fijo, sin desglose ni respaldo vinculado.",
                              impacto="No se puede verificar ni negociar el monto; suele esconder subcontratos sin cotizar."))

        cd = sum(p.total or 0 for p in partidas)
        total_gl = sum(p.total or 0 for p in partidas if norm_unidad(p.unidad) in UNIDADES_GLOBALES)
        if cd and total_gl / cd > 0.25:
            h.append(Hallazgo(tipo="alta_proporcion_global", severidad="alta", hoja=pres,
                              descripcion=f"Las partidas globales suman el {total_gl / cd:.0%} del costo directo.",
                              impacto="Una parte grande del presupuesto no tiene cantidades ni precios unitarios: baja trazabilidad y alto riesgo de desvio."))

        # Formula de total distinta al resto
        celdas_total = [m.celda(pres, f"{_col_total(m, pres)}{p.fila}") for p in partidas]
        firmas = Counter(firma_relativa(c) for c in celdas_total if c and c.formula)
        if firmas:
            mayoria, n = firmas.most_common(1)[0]
            distintas = [c for c in celdas_total if c and c.formula and firma_relativa(c) != mayoria]
            if n >= 3 and distintas:
                h.append(Hallazgo(tipo="formula_inconsistente", severidad="alta", hoja=pres, celdas=[c.coord for c in distintas],
                                  descripcion=f"{len(distintas)} total(es) de partida calculados con una fórmula distinta al resto "
                                              f"({', '.join(f'{c.coord}: {c.formula}' for c in distintas[:3])}).",
                                  impacto="Recargos o ajustes ocultos que nadie ve al revisar el itemizado."))
            constantes = [p for p, c in zip(partidas, celdas_total) if c and not c.formula and _num(c) is not None]
            if constantes and n >= 3:
                h.append(Hallazgo(tipo="total_escrito_a_mano", severidad="alta", hoja=pres,
                                  celdas=[f"{p.item} {p.partida}" for p in constantes],
                                  descripcion=f"{len(constantes)} total(es) de partida escritos a mano en una columna de fórmulas.",
                                  impacto="Si cambia la cantidad o el precio, el total no se actualiza."))

        # Partidas duplicadas
        conteo = Counter(norm(p.partida) for p in partidas)
        for nombre, n in conteo.items():
            if n > 1:
                dup = [p for p in partidas if norm(p.partida) == nombre]
                h.append(Hallazgo(tipo="partida_duplicada", severidad="media", hoja=pres,
                                  celdas=[f"{p.item} (fila {p.fila})" for p in dup],
                                  descripcion=f"La partida '{dup[0].partida}' aparece {n} veces.",
                                  impacto="Riesgo de cobrar dos veces lo mismo o de que una de ellas debía ser otra partida."))

        # Unidades escritas distinto
        formas = defaultdict(set)
        for p in partidas:
            formas[norm_unidad(p.unidad)].add(p.unidad.strip())
        variantes = {k: v for k, v in formas.items() if len(v) > 1}
        if variantes:
            h.append(Hallazgo(tipo="unidad_inconsistente", severidad="baja", hoja=pres,
                              celdas=[" / ".join(sorted(v)) for v in variantes.values()],
                              descripcion="La misma unidad está escrita de formas distintas.",
                              impacto="Impide sumar o comparar cantidades automáticamente y confunde a quien cubica."))

        # Cuadre: costo directo declarado vs suma de partidas
        cd_decl = next((v for k, (_, v) in pie.items() if k.startswith("costo directo")), None)
        if cd_decl and cd and abs(cd_decl - cd) / cd > 0.005:
            h.append(Hallazgo(tipo="descuadre_totales", severidad="alta", hoja=pres,
                              descripcion=f"El costo directo declarado ({cd_decl:,.0f}) no cuadra con la suma de partidas ({cd:,.0f}).",
                              impacto="Partidas fuera de los subtotales o subtotales mal definidos."))

    # Numeros fijos dentro de formulas (deberian ser parametros)
    magicos = defaultdict(list)
    for c in m.formulas:
        for n in c.numeros:
            if n not in NUMEROS_NEUTROS:
                magicos[n].append(c.id)
    for valor, celdas in sorted(magicos.items()):
        h.append(Hallazgo(tipo="numero_magico", severidad="media", hoja=celdas[0].split("!")[0], celdas=celdas[:15],
                          descripcion=f"El valor {valor:g} está escrito dentro de {len(celdas)} fórmula(s).",
                          impacto="Si cambia (IVA, recargo, porcentaje), hay que encontrarlo a mano en cada fórmula."))

    # APU: unidad de la linea distinta a la del insumo, y APU que nadie usa
    if bloques:
        usados = {p.apu.replace("$", "") for p in partidas if p.apu}
        for pu_id, b in bloques.items():
            if pu_id not in usados:
                h.append(Hallazgo(tipo="apu_sin_uso", severidad="baja", hoja=pu_id.split("!")[0],
                                  celdas=[pu_id], descripcion=f"El APU '{b['nombre']}' no se usa en el presupuesto.",
                                  impacto="Análisis desactualizados que confunden y pueden copiarse por error."))
            for linea in b["lineas"]:
                u = linea["unidad"]
                if u is None or u.formula:
                    continue
                insumo = _insumo_de_linea(m, linea)
                if insumo and norm_unidad(insumo[1]) != norm_unidad(u.valor):
                    h.append(Hallazgo(tipo="unidad_inconsistente", severidad="media", hoja=u.hoja, celdas=[u.coord],
                                      descripcion=f"En el APU '{b['nombre']}', '{insumo[0]}' figura en {u.valor} "
                                                  f"pero el insumo se compra en {insumo[1]}.",
                                      impacto="El rendimiento se interpreta en la unidad equivocada: el costo del APU puede estar muy errado."))
    orden = {s: i for i, s in enumerate(SEVERIDADES)}
    return sorted(h, key=lambda x: (orden[x.severidad], x.tipo))


def _col_total(m: ModeloExcel, hoja: str) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(_encabezado_presupuesto(m, hoja)[1]["total"])


def _insumo_de_linea(m: ModeloExcel, linea: dict) -> tuple[str, str] | None:
    """(nombre, unidad) del insumo al que apunta el precio de una linea de APU."""
    celda_parcial: Celda | None = linea["celda_parcial"]
    if not celda_parcial:
        return None
    fila = linea["fila"]
    precio = next((c for c in m.de_hoja(celda_parcial.hoja) if c.fila == fila and c.formula
                   and any(r.hoja != c.hoja for r in c.referencias)), None)
    if not precio:
        return None
    ref = next(r for r in precio.referencias if r.hoja != precio.hoja)
    fila_ins = int(re.sub(r"[A-Z$]", "", ref.rango.split(":")[0]))
    celdas = {c.col: c for c in m.de_hoja(ref.hoja) if c.fila == fila_ins}
    textos = [c.valor for _, c in sorted(celdas.items()) if isinstance(c.valor, str)]
    return (textos[1] if len(textos) > 1 else textos[0], textos[-1]) if len(textos) >= 2 else None


# ---------- Indicadores ----------

def _pareto(partidas: list[Partida]) -> None:
    cd = sum(p.total or 0 for p in partidas)
    acumulado = 0.0
    for p in sorted(partidas, key=lambda x: x.total or 0, reverse=True):
        p.participacion = (p.total or 0) / cd if cd else 0
        acumulado += p.participacion
        p.clase = "A" if acumulado - p.participacion < 0.80 else ("B" if acumulado - p.participacion < 0.95 else "C")


def _composicion(partidas: list[Partida], bloques: dict) -> dict:
    """Reparte el costo de las partidas con APU entre materiales, mano de obra y equipos."""
    comp = {"materiales": 0.0, "mano_obra": 0.0, "equipos": 0.0, "sin_apu": 0.0}
    for p in partidas:
        b = bloques.get((p.apu or "").replace("$", ""))
        if not b or not p.total:
            comp["sin_apu"] += p.total or 0
            continue
        pu = sum(l["parcial"] or 0 for l in b["lineas"]) or 1
        factor = p.total / pu  # incluye recargos que no esten en el APU
        for l in b["lineas"]:
            nombre = norm(l["insumo"]) + " " + norm(l["unidad"].valor if l["unidad"] else "")
            clase = "mano_obra" if MANO_OBRA.search(nombre) else ("equipos" if EQUIPOS.search(nombre) else "materiales")
            comp[clase] += (l["parcial"] or 0) * factor
    return {k: round(v, 2) for k, v in comp.items()}


def analizar(ruta: Path) -> AnalisisPresupuesto | None:
    m = cargar(ruta)
    roles = _roles(m)
    if "presupuesto" not in roles:
        return None
    partidas, pie = _partidas(m, roles["presupuesto"])
    bloques = _bloques_apu(m, roles["apu"]) if "apu" in roles else {}
    _pareto(partidas)
    etiquetas = _metricas_etiquetadas(m)
    cd = sum(p.total or 0 for p in partidas)
    total = next((v for k, (_, v) in pie.items() if k == "total" or k.startswith("total ")), None)
    sup_c, sup_v = etiquetas.get("superficie_construida"), etiquetas.get("superficie_vendible")
    metricas = {
        "costo_directo": round(cd, 2),
        "total": round(total, 2) if total else None,
        "superficie_construida": sup_c, "superficie_vendible": sup_v,
        "costo_m2": round(cd / sup_c, 2) if sup_c else etiquetas.get("costo_m2"),
        "venta_m2": round(total / sup_v, 2) if (total and sup_v) else etiquetas.get("venta_m2"),
        "partidas": len(partidas),
        "partidas_con_apu": sum(1 for p in partidas if p.apu),
        "partidas_clase_a": sum(1 for p in partidas if p.clase == "A"),
        **{PIE.get(k, k.replace(" ", "_")): round(v, 2) for k, (_, v) in pie.items() if v is not None and k != "total"},
    }
    capitulos = defaultdict(float)
    for p in partidas:
        capitulos[p.capitulo or "(sin capitulo)"] += p.total or 0
    hallazgos = _hallazgos(m, roles, partidas, bloques, pie, metricas)
    if m.sin_valores():
        hallazgos.insert(0, Hallazgo(tipo="sin_valores_calculados", severidad="media", hoja="",
                                     descripcion="El libro no trae valores calculados y no se pudo recalcular (falta LibreOffice).",
                                     impacto="Se analizó solo la estructura; los montos no están disponibles."))
    return AnalisisPresupuesto(
        archivo=str(ruta.name), hojas=roles, recalculado=m.recalculado, metricas=metricas,
        modelo={"formulas": len(m.formulas), "hojas": m.hojas, "profundidad_calculo": m.profundidad(),
                "referencias_entre_hojas": sum(1 for c in m.formulas if any(r.hoja != c.hoja for r in c.referencias)),
                "apu": len(bloques)},
        partidas=sorted(partidas, key=lambda p: p.fila),
        capitulos=[{"capitulo": k, "total": round(v, 2), "participacion": round(v / cd, 4) if cd else 0}
                   for k, v in sorted(capitulos.items(), key=lambda kv: -kv[1])],
        composicion=_composicion(partidas, bloques),
        hallazgos=hallazgos,
    )


def analizar_cliente(cliente) -> dict:
    """Analiza todos los Excel de raw/ que tengan estructura de presupuesto -> processed/presupuestos.json."""
    resultados = []
    for ruta in sorted(cliente.raw.rglob("*")):
        if ruta.suffix.lower() not in (".xlsx", ".xlsm") or ruta.name.startswith("~$"):
            continue
        try:
            a = analizar(ruta)
        except Exception as e:  # un libro corrupto no detiene el diagnostico
            log.warning("No se pudo analizar %s: %s", ruta.name, e)
            continue
        if a:
            a.archivo = str(ruta.relative_to(cliente.raw))
            resultados.append(a.model_dump())
    (cliente.processed / "presupuestos.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"presupuestos": len(resultados), "hallazgos": sum(len(r["hallazgos"]) for r in resultados)}
