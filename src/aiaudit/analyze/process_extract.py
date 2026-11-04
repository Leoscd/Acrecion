"""Fragmentos -> procesos de trabajo candidatos.

Modo LLM (openai/ollama): lotes de fragmentos con la taxonomia del sector como guia.
Modo offline: coincidencia de palabras clave de la taxonomia (sin API; sirve para pruebas y como linea base).
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

from aiaudit.analyze.consolidate import _norm
from aiaudit.config import Cliente, leer_jsonl, taxonomia
from aiaudit.schemas import ExtraccionLLM, Fragmento, ProcesoLLM

log = logging.getLogger(__name__)

SISTEMA = """Eres analista de procesos de negocio en una consultora de integracion de IA.
Recibes fragmentos de archivos de una empresa del sector "{sector}" (documentos, planillas, correos, chats, transcripciones).
Tu tarea: identificar PROCESOS DE TRABAJO RECURRENTES que consumen tiempo de personas.

Reglas:
- Solo procesos con evidencia en los fragmentos. No inventes. Si no hay procesos, devuelve una lista vacia.
- Actores como ROLES (ej: "jefe de obra", "administrativo"), nunca nombres. Los tokens <PERSONA_n> son personas anonimizadas: infiere su rol si es posible.
- Pasos en orden; marca manual=true si lo hace una persona sin automatizacion (copiar, digitar, revisar, reenviar, llamar).
- Herramientas: software o medios usados (Excel, WhatsApp, correo, papel, ERP, AutoCAD...).
- Dolores: demoras, errores, retrabajo, informacion dispersa, dependencia de una persona. Cita brevemente la evidencia.
- evidencias: ids de los fragmentos (el valor entre corchetes) que respaldan el proceso.
- id_taxonomia: si el proceso corresponde a uno del catalogo, usa su id; si no, "".
- confianza: 0.9+ evidencia directa y repetida; 0.5 indicios; <0.4 no lo incluyas.
- frecuencia: diaria | semanal | mensual | por_proyecto | desconocida.
- Responde en español con ortografía correcta (tildes y ñ).

Catalogo de procesos tipo del sector:
{catalogo}"""

HERRAMIENTAS = ["excel", "planilla", "whatsapp", "correo", "email", "gmail", "outlook", "papel", "pdf", "word",
                "erp", "sap", "autocad", "revit", "bim", "drive", "teams", "zoom", "telefono", "crm", "sistema"]
DOLORES = re.compile(r"[^.\n]*(atras|demora|error|equivoc|manual|retrabajo|de nuevo|otra vez|urgente|falta|perd|"
                     r"no llega|tarde|duplic|a mano|copiar|digitar)[^.\n]*", re.I)


def _catalogo(tax: list[dict]) -> str:
    return "\n".join(f"- {p['id']}: {p['nombre']} (senales: {', '.join(p['palabras_clave'])})" for p in tax)


def lotes(frags: list[Fragmento], max_chars: int) -> list[str]:
    """Agrupa fragmentos (ordenados por archivo) en bloques de hasta max_chars."""
    bloques, actual = [], ""
    for f in sorted(frags, key=lambda f: (f.origen, f.id)):
        cab = f"[{f.id}] ({f.tipo_fuente}, {f.origen}{', ' + f.fecha if f.fecha else ''})"
        pieza = f"{cab}\n{f.texto}\n\n"
        if len(actual) + len(pieza) > max_chars and actual:
            bloques.append(actual)
            actual = ""
        actual += pieza[:max_chars]
    return bloques + [actual] if actual else bloques


def extraer_llm(frags: list[Fragmento], sector: str, cfg_llm: dict, llm) -> list[ProcesoLLM]:
    sistema = SISTEMA.format(sector=sector, catalogo=_catalogo(taxonomia(sector)))
    procesos = []
    bloques = lotes(frags, cfg_llm["max_chars_por_lote"])
    for i, bloque in enumerate(bloques, 1):
        log.info("LLM lote %d/%d (%d caracteres)", i, len(bloques), len(bloque))
        try:
            procesos += llm.estructurado(sistema, f"Fragmentos:\n\n{bloque}", ExtraccionLLM).procesos
        except Exception as e:
            log.warning("Lote %d fallo: %s", i, e)
    return [p for p in procesos if p.confianza >= 0.4]


def extraer_offline(frags: list[Fragmento], sector: str) -> list[ProcesoLLM]:
    procesos = []
    for t in taxonomia(sector):
        claves = [_norm(k) for k in t["palabras_clave"]]  # coincidencia sin tildes ni mayusculas
        evidencias, golpes, herramientas, dolores = [], 0, set(), []
        for f in frags:
            txt = _norm(f.texto)
            n = sum(txt.count(k) for k in claves)
            if n:
                golpes += n
                evidencias.append(f.id)
                herramientas |= {h for h in HERRAMIENTAS if h in txt}
                herramientas |= {"whatsapp"} if f.tipo_fuente == "chat" else set()
                herramientas |= {"correo"} if f.tipo_fuente == "correo" else set()
                herramientas |= {"excel"} if f.tipo_fuente == "tabla" else set()
                dolores += [m.group().strip()[:160] for m in DOLORES.finditer(f.texto)]
        if golpes >= 2 and len(evidencias) >= 1:
            procesos.append(ProcesoLLM(
                id_taxonomia=t["id"], nombre=t["nombre"], area=t["area"], descripcion=f"Detectado por palabras clave ({golpes} coincidencias).",
                actores=[], herramientas=sorted(herramientas), pasos=[], frecuencia="desconocida",
                dolores=list(dict.fromkeys(dolores))[:5], evidencias=evidencias[:20], confianza=round(min(0.9, 0.3 + golpes / 20), 2),
            ))
    return procesos


def extraer(cliente: Cliente) -> dict:
    s = cliente.settings
    cfg_llm, anonimizar = s["llm"], s["privacidad"]["anonimizar"]
    ruta = cliente.fragmentos_anon if anonimizar else cliente.fragmentos
    if not ruta.exists():
        raise FileNotFoundError(f"No existe {ruta.name}: ejecutar antes 'ingest'{' y anonymize' if anonimizar else ''}")
    frags = leer_jsonl(ruta, Fragmento)

    if cfg_llm["provider"] == "offline":
        crudos, fuente, costo = extraer_offline(frags, cliente.sector), "offline", 0.0
    else:
        from aiaudit.llm.client import LLM
        if cfg_llm["provider"] == "openai" and not anonimizar:
            log.warning("ATENCIÓN: se enviarán datos SIN anonimizar a un proveedor externo")
        llm = LLM(cfg_llm, cliente.interim / "llm_log.jsonl")
        crudos, fuente = extraer_llm(frags, cliente.sector, cfg_llm, llm), "llm"
        costo = llm.costo_usd
        log.info("Tokens: %d in / %d out | costo estimado USD %.4f", llm.tokens_in, llm.tokens_out, costo)

    salida = [{"fuente": fuente, **p.model_dump()} for p in crudos]
    (cliente.processed / "procesos_crudos.json").write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    por_fuente = defaultdict(int)
    for f in frags:
        por_fuente[f.tipo_fuente] += 1
    return {"procesos_crudos": len(crudos), "fuente": fuente, "costo_usd": round(costo, 4), "fragmentos": dict(por_fuente)}
