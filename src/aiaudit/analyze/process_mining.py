"""Process mining con pm4py sobre las tablas detectadas como log de eventos (case_id, activity, timestamp).

Mide el flujo REAL: variantes, duracion de casos, cuellos de botella y retrabajo.
Es la evidencia mas solida del diagnostico porque viene de registros, no de opiniones.
"""
from __future__ import annotations

import json
import logging
import warnings

import pandas as pd

from aiaudit.config import Cliente, taxonomia
from aiaudit.schemas import PasoLLM, ProcesoLLM

log = logging.getLogger(__name__)


def _horas(seg: float) -> float:
    return round(seg / 3600, 1)


def analizar_log(df: pd.DataFrame) -> dict:
    import pm4py

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = pm4py.format_dataframe(df, case_id="case_id", activity_key="activity", timestamp_key="timestamp")
        variantes = pm4py.get_variants(df)
        dfg, _, _ = pm4py.discover_performance_dfg(df)
        duraciones = pm4py.get_all_case_durations(df)
    n_casos = df["case_id"].nunique()
    top = sorted(variantes.items(), key=lambda kv: kv[1], reverse=True)[:3]
    cuellos = sorted(dfg.items(), key=lambda kv: kv[1]["mean"], reverse=True)[:3]
    repetidas = df.groupby("case_id")["activity"].apply(lambda s: s.duplicated().any())
    return {
        "casos": int(n_casos),
        "eventos": int(len(df)),
        "actividades": sorted(df["activity"].unique().tolist()),
        "recursos": sorted(df["resource"].unique().tolist()) if "resource" in df else [],
        "variantes": len(variantes),
        "top_variantes": [{"secuencia": list(v), "casos": int(n), "pct": round(n / n_casos, 2)} for v, n in top],
        "duracion_media_h": _horas(sum(duraciones) / len(duraciones)) if duraciones else 0,
        "duracion_max_h": _horas(max(duraciones)) if duraciones else 0,
        "cuellos_botella": [{"de": a, "a": b, "espera_media_h": _horas(m["mean"])} for (a, b), m in cuellos],
        "casos_con_retrabajo_pct": round(float(repetidas.mean()), 2),
    }


def _match_taxonomia(texto: str, sector: str) -> dict | None:
    texto = texto.lower()
    puntajes = [(sum(k in texto for k in t["palabras_clave"]), t) for t in taxonomia(sector)]
    mejor = max(puntajes, key=lambda x: x[0], default=(0, None))
    return mejor[1] if mejor[0] else None


def a_proceso(nombre: str, m: dict, sector: str) -> ProcesoLLM:
    principal = m["top_variantes"][0]["secuencia"] if m["top_variantes"] else m["actividades"]
    t = _match_taxonomia(nombre + " " + " ".join(m["actividades"]), sector)
    dolores = [f"Espera media de {c['espera_media_h']} h entre '{c['de']}' y '{c['a']}'" for c in m["cuellos_botella"]]
    if m["casos_con_retrabajo_pct"] > 0:
        dolores.append(f"{m['casos_con_retrabajo_pct']:.0%} de los casos repite alguna actividad (retrabajo)")
    if m["variantes"] > 1:
        dolores.append(f"{m['variantes']} variantes distintas del flujo: el proceso no esta estandarizado")
    return ProcesoLLM(
        id_taxonomia=t["id"] if t else "",
        nombre=t["nombre"] if t else f"Flujo registrado en {nombre}",
        area=t["area"] if t else "",
        descripcion=(f"Flujo medido en {m['casos']} casos y {m['eventos']} eventos. "
                     f"Duracion media {m['duracion_media_h']} h (max {m['duracion_max_h']} h)."),
        actores=m["recursos"][:10], herramientas=["registro tabular"],
        pasos=[PasoLLM(orden=i, descripcion=a, actor="", herramienta="", manual=True) for i, a in enumerate(principal, 1)],
        frecuencia="desconocida", dolores=dolores, evidencias=[f"log:{nombre}"], confianza=0.95,
    )


def minar(cliente: Cliente) -> dict:
    archivos = sorted(cliente.tablas_log.glob("*.csv")) if cliente.tablas_log.exists() else []
    resultados, procesos = {}, []
    for ruta in archivos:
        try:
            df = pd.read_csv(ruta, parse_dates=["timestamp"])
            resultados[ruta.stem] = analizar_log(df)
            procesos.append({"fuente": "mining", **a_proceso(ruta.stem, resultados[ruta.stem], cliente.sector).model_dump()})
        except ImportError:
            log.warning("pm4py no instalado: se omite process mining (pip install aiaudit[mining])")
            break
        except Exception as e:
            log.warning("Mining fallo en %s: %s", ruta.name, e)
    (cliente.processed / "mining.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (cliente.processed / "procesos_mining.json").write_text(json.dumps(procesos, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"logs": len(archivos), "procesos": len(procesos)}
