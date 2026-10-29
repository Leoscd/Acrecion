"""Une procesos detectados (LLM/offline + mining), elimina duplicados y cruza con el cuestionario."""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher

from aiaudit.config import Cliente, taxonomia
from aiaudit.ingest.base import slug
from aiaudit.schemas import Diagnostico, Proceso, ProcesoLLM


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", t).strip()


def _similar(a: str, b: str, umbral: float = 0.72) -> bool:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= umbral


def _unicos(*listas) -> list:
    vistos, out = set(), []
    for x in (y for l in listas for y in l):
        k = _norm(str(x))
        if k and k not in vistos:
            vistos.add(k)
            out.append(x)
    return out


def fusionar(crudos: list[tuple[str, ProcesoLLM]]) -> list[Proceso]:
    """Agrupa por id de taxonomia o por nombre similar."""
    grupos: list[list[tuple[str, ProcesoLLM]]] = []
    for fuente, p in crudos:
        destino = next((g for g in grupos if
                        (p.id_taxonomia and p.id_taxonomia == g[0][1].id_taxonomia) or
                        (not p.id_taxonomia and not g[0][1].id_taxonomia and _similar(p.nombre, g[0][1].nombre))), None)
        (destino.append((fuente, p)) if destino is not None else grupos.append([(fuente, p)]))

    procesos = []
    for g in grupos:
        ps = [p for _, p in g]
        base = max(ps, key=lambda p: (p.confianza, len(p.pasos)))
        con_pasos = max(ps, key=lambda p: len(p.pasos))
        # la confianza sube si varias fuentes/lotes independientes coinciden
        confianza = min(0.99, max(p.confianza for p in ps) + 0.05 * (len(ps) - 1))
        procesos.append(Proceso(
            id=base.id_taxonomia or slug(base.nombre),
            nombre=base.nombre, area=base.area,
            descripcion=" ".join(_unicos([p.descripcion for p in ps]))[:1200],
            actores=_unicos(*[p.actores for p in ps]), herramientas=_unicos(*[p.herramientas for p in ps]),
            pasos=con_pasos.pasos,
            frecuencia=next((p.frecuencia for p in ps if p.frecuencia != "desconocida"), "desconocida"),
            dolores=_unicos(*[p.dolores for p in ps])[:10], evidencias=_unicos(*[p.evidencias for p in ps])[:30],
            fuentes_deteccion=_unicos([f for f, _ in g]), confianza=round(confianza, 2),
        ))
    return procesos


def cruzar_cuestionario(procesos: list[Proceso], cuestionario: dict, sector: str) -> list[Proceso]:
    """Agrega horas/costos declarados por el cliente. Procesos declarados y no detectados tambien entran."""
    tax = {t["id"]: t for t in taxonomia(sector)}
    for q in cuestionario.get("procesos") or []:
        if not (q.get("id") or q.get("nombre")):
            continue
        p = next((p for p in procesos if (q.get("id") and q["id"] == p.id) or
                  (q.get("nombre") and _similar(q["nombre"], p.nombre, 0.6))), None)
        if p is None:
            t = tax.get(q.get("id"), {})
            p = Proceso(id=q.get("id") or slug(q["nombre"]), nombre=q.get("nombre") or t.get("nombre", q["id"]),
                        area=t.get("area", ""), descripcion="Declarado por el cliente en la entrevista; sin evidencia documental.",
                        confianza=0.5)
            procesos.append(p)
        p.horas_mes = q.get("horas_mes") or None
        p.personas = q.get("personas") or None
        p.costo_hora = q.get("costo_hora")
        p.dolor = q.get("dolor")
        if q.get("responsable"):
            p.actores = _unicos([q["responsable"]], p.actores)
        if q.get("notas"):
            p.dolores = _unicos(p.dolores, [f"(cliente) {q['notas']}"])
        p.fuentes_deteccion = _unicos(p.fuentes_deteccion, ["cuestionario"])
    return procesos


def mermaid(p: Proceso) -> str:
    """Diagrama de flujo: pasos manuales en naranja, automatizados en verde."""
    if not p.pasos:
        return ""
    lineas = ["flowchart LR"]
    for paso in sorted(p.pasos, key=lambda x: x.orden):
        etiqueta = paso.descripcion.replace('"', "'")[:60]
        extra = f"<br/><i>{paso.actor}</i>" if paso.actor else ""
        lineas.append(f'  P{paso.orden}["{etiqueta}{extra}"]:::{"manual" if paso.manual else "auto"}')
    orden = [x.orden for x in sorted(p.pasos, key=lambda x: x.orden)]
    lineas += [f"  P{a} --> P{b}" for a, b in zip(orden, orden[1:])]
    lineas += ["  classDef manual fill:#fde2c8,stroke:#d9822b", "  classDef auto fill:#d4edda,stroke:#28a745"]
    return "\n".join(lineas)


def _leer_crudos(ruta) -> list[tuple[str, ProcesoLLM]]:
    if not ruta.exists():
        return []
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return [(d.pop("fuente"), ProcesoLLM.model_validate(d)) for d in datos]


def consolidar(cliente: Cliente) -> dict:
    crudos = _leer_crudos(cliente.processed / "procesos_crudos.json") + _leer_crudos(cliente.processed / "procesos_mining.json")
    procesos = cruzar_cuestionario(fusionar(crudos), cliente.cuestionario, cliente.sector)
    for p in procesos:
        p.diagrama_mermaid = mermaid(p)
    procesos.sort(key=lambda p: p.confianza, reverse=True)
    inventario = cliente.interim / "inventario.json"
    resumen = {}
    if inventario.exists():
        for i in json.loads(inventario.read_text(encoding="utf-8")):
            resumen[i["tipo"]] = resumen.get(i["tipo"], 0) + 1
    diag = Diagnostico(cliente=cliente.nombre, sector=cliente.sector, procesos=procesos, resumen_fuentes=resumen)
    cliente.diagnostico.write_text(diag.model_dump_json(indent=2), encoding="utf-8")
    return {"procesos": len(procesos), "validados_cuestionario": sum(p.horas_mes is not None for p in procesos)}
