"""Anonimizacion de PII antes de enviar texto a un LLM externo.

Regex (siempre): correos, telefonos, RUT, DNI/CUIT, tarjetas, IBAN.
Presidio + spaCy (opcional): nombres de personas.
Reemplazo consistente: la misma entidad recibe siempre el mismo token (<PERSONA_3>), asi el LLM
puede seguir quien hace que. El mapa de reversion se guarda solo en disco local.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from aiaudit.config import Cliente, escribir_jsonl, leer_jsonl
from aiaudit.schemas import Fragmento

log = logging.getLogger(__name__)

# El grupo "v" es lo que se reemplaza; el resto es contexto (etiquetas como "telefono:" se conservan).
# Telefonos y DNI exigen prefijo "+" o etiqueta, para no confundirlos con montos de presupuestos.
PATRONES = {
    "EMAIL": re.compile(r"(?P<v>[\w.+-]+@[\w-]+\.[\w.-]+)"),
    "TARJETA": re.compile(r"\b(?P<v>(?:\d{4}[ -]){3}\d{4})\b"),
    "IBAN": re.compile(r"\b(?P<v>[A-Z]{2}\d{2}[A-Z0-9]{11,30})\b"),
    "RUT": re.compile(r"\b(?P<v>\d{1,2}\.?\d{3}\.?\d{3}-[\dkK])\b"),
    "CUIT": re.compile(r"\b(?P<v>\d{2}-\d{8}-\d)\b"),
    "TELEFONO": re.compile(r"(?P<v>\+\d{1,3}[ .-]?\(?\d{1,4}\)?[ .-]?\d{3,4}[ .-]?\d{3,4})\b"
                           r"|(?:tel[eé]fono|fono|cel(?:ular)?|m[oó]vil|whatsapp)\.?:?\s*(?P<v2>\d[\d ()-]{6,14}\d)", re.I),
    "DNI": re.compile(r"(?:dni|c\.i\.|c[eé]dula)\.?:?\s*(?P<v>\d{1,2}\.?\d{3}\.?\d{3}[A-Z]?)\b", re.I),
}
MODELOS_SPACY = {"es": ["es_core_news_md", "es_core_news_sm"], "en": ["en_core_web_md", "en_core_web_sm"]}


@lru_cache(maxsize=1)
def _presidio(idioma: str):
    """AnalyzerEngine con el primer modelo spaCy disponible, o None si no hay."""
    try:
        import spacy
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
    except ImportError:
        return None
    modelo = next((m for m in MODELOS_SPACY.get(idioma, []) if spacy.util.is_package(m)), None)
    if not modelo:
        log.warning("Sin modelo spaCy para '%s': solo se anonimiza por regex. Instalar: python -m spacy download %s",
                    idioma, MODELOS_SPACY.get(idioma, ["?"])[-1])
        return None
    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy", "models": [{"lang_code": idioma, "model_name": modelo}]}).create_engine()
    return AnalyzerEngine(nlp_engine=nlp, supported_languages=[idioma])


def _limpiar_span(t: str) -> str:
    """'Pedro Soto: Ok' -> 'Pedro Soto'. Corta en separadores de chat/linea y quita puntuacion."""
    return re.split(r"[:\n,;()]", t)[0].strip(" .-'\"")


def _es_nombre(t: str) -> bool:
    """2-4 palabras capitalizadas, sin siglas ni digitos."""
    palabras = t.split()
    return (2 <= len(palabras) <= 4 and all(w[0].isupper() and not w.isupper() for w in palabras)
            and not any(c.isdigit() for c in t))


class Anonimizador:
    """Dos pasadas: aprender() recolecta nombres completos del corpus (NER + participantes de chat);
    texto() reemplaza esos nombres y sus partes (nombre o apellido solos) con el mismo token.
    Las entidades NER de una sola palabra se ignoran: en espanol generan muchos falsos positivos
    ("Partida", "EPP"). Para nombres sueltos que nunca aparecen completos, usar privacidad.nombres_extra."""

    def __init__(self, idioma: str = "es", usar_presidio: bool = True, mapa: dict | None = None):
        self.idioma = idioma
        self.motor = _presidio(idioma) if usar_presidio else None
        self.mapa: dict[str, str] = mapa or {}       # valor original -> token
        self._conteo: dict[str, int] = {}
        for token in self.mapa.values():
            tipo, n = token.strip("<>").rsplit("_", 1)
            self._conteo[tipo] = max(self._conteo.get(tipo, 0), int(n))
        self._patron_nombres: re.Pattern | None = None

    def _token(self, valor: str, tipo: str) -> str:
        clave = valor.strip()
        if clave not in self.mapa:
            self._conteo[tipo] = self._conteo.get(tipo, 0) + 1
            self.mapa[clave] = f"<{tipo}_{self._conteo[tipo]}>"
        return self.mapa[clave]

    def aprender(self, textos: list[str], nombres_extra: list[str] = ()):
        nombres = {n.strip() for n in nombres_extra if n.strip()}
        if self.motor:
            for t in textos:
                for r in self.motor.analyze(t, language=self.idioma, entities=["PERSON"], score_threshold=0.6):
                    if _es_nombre(n := _limpiar_span(t[r.start:r.end])):
                        nombres.add(n)
        # cada nombre completo recibe un token; sus partes (>3 letras) apuntan al mismo token si no son ambiguas
        partes: dict[str, set[str]] = {}
        for n in sorted(nombres, key=len, reverse=True):
            token = self.mapa.get(n) or self._token(n, "PERSONA")
            for w in n.split():
                if len(w) > 3:
                    partes.setdefault(w, set()).add(token)
        for w, tokens in partes.items():
            if len(tokens) == 1 and w not in self.mapa:
                self.mapa[w] = next(iter(tokens))
        claves = sorted((k for k, v in self.mapa.items() if v.startswith("<PERSONA_")), key=len, reverse=True)
        if claves:
            self._patron_nombres = re.compile(r"\b(" + "|".join(map(re.escape, claves)) + r")\b")

    def texto(self, texto: str) -> str:
        if not texto:
            return texto
        if self._patron_nombres:
            texto = self._patron_nombres.sub(lambda m: self.mapa[m.group()], texto)
        for tipo, patron in PATRONES.items():
            texto = patron.sub(lambda m: self._reemplazar(m, tipo), texto)
        return texto

    def _reemplazar(self, m: re.Match, tipo: str) -> str:
        grupo = "v" if m.group("v") else "v2"
        ini, fin = m.start(grupo) - m.start(), m.end(grupo) - m.start()
        return m.group()[:ini] + self._token(m.group(grupo), tipo) + m.group()[fin:]

    def fragmento(self, f: Fragmento) -> Fragmento:
        autor = self.texto(f.autor) if f.autor else None
        meta = {**f.metadatos, "participantes": [self.texto(x) for x in f.metadatos.get("participantes", [])]}
        return f.model_copy(update={"texto": self.texto(f.texto), "autor": autor, "metadatos": meta})


def revertir(texto: str, mapa: dict[str, str]) -> str:
    """Devuelve el texto original usando el valor mas largo asociado a cada token."""
    inverso: dict[str, str] = {}
    for original, token in mapa.items():
        if len(original) > len(inverso.get(token, "")):
            inverso[token] = original
    for token, original in inverso.items():
        texto = texto.replace(token, original)
    return texto


def anonimizar(cliente: Cliente) -> dict:
    cfg = cliente.settings["privacidad"]
    ruta_mapa = cliente.interim / "mapa_anonimizacion.json"
    mapa = json.loads(ruta_mapa.read_text(encoding="utf-8")) if ruta_mapa.exists() else {}
    anon = Anonimizador(cfg["idioma"], cfg["usar_presidio"], mapa)
    originales = leer_jsonl(cliente.fragmentos, Fragmento)
    participantes = [p for f in originales for p in f.metadatos.get("participantes", [])]
    anon.aprender([f.texto for f in originales], [*participantes, *(cfg.get("nombres_extra") or [])])
    frags = [anon.fragmento(f) for f in originales]
    escribir_jsonl(cliente.fragmentos_anon, frags)
    ruta_mapa.write_text(json.dumps(anon.mapa, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fragmentos": len(frags), "entidades": len(anon.mapa), "presidio": anon.motor is not None}
