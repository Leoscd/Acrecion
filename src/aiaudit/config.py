"""Carga de configuracion y rutas por cliente."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
CONFIG_DIR = RAIZ / "config"
DATA_DIR = RAIZ / "data"


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def leer_yaml(ruta: Path) -> dict:
    if not ruta.exists():
        return {}
    return yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}


@dataclass
class Cliente:
    """Rutas y configuracion de un cliente: data/<nombre>/{raw,interim,processed,outputs}."""
    nombre: str
    base: Path
    overrides: dict = field(default_factory=dict)  # ej: {"llm": {"provider": "offline"}} desde la CLI

    @classmethod
    def desde_nombre(cls, nombre: str, data_dir: Path = DATA_DIR, overrides: dict | None = None) -> "Cliente":
        c = cls(nombre, data_dir / nombre, overrides or {})
        for d in (c.raw, c.interim, c.processed, c.outputs):
            d.mkdir(parents=True, exist_ok=True)
        return c

    raw = property(lambda s: s.base / "raw")
    interim = property(lambda s: s.base / "interim")
    processed = property(lambda s: s.base / "processed")
    outputs = property(lambda s: s.base / "outputs")

    # contratos entre etapas
    fragmentos = property(lambda s: s.interim / "fragmentos.jsonl")
    fragmentos_anon = property(lambda s: s.interim / "fragmentos_anon.jsonl")
    tablas_log = property(lambda s: s.interim / "logs_eventos")
    diagnostico = property(lambda s: s.processed / "diagnostico.json")

    @property
    def settings(self) -> dict:
        return _merge(_merge(leer_yaml(CONFIG_DIR / "settings.yaml"), leer_yaml(self.base / "settings.yaml")), self.overrides)

    @property
    def cuestionario(self) -> dict:
        return leer_yaml(self.base / "cuestionario.yaml")

    @property
    def sector(self) -> str:
        return (self.cuestionario.get("empresa") or {}).get("sector") or "pyme"


def taxonomia(sector: str) -> list[dict]:
    """Procesos tipo del sector; construccion incluye tambien los procesos pyme transversales."""
    tax = leer_yaml(CONFIG_DIR / "taxonomia_procesos.yaml")
    procesos = list(tax.get(sector, []))
    if sector != "pyme":
        procesos += tax.get("pyme", [])
    return procesos


def escribir_jsonl(ruta: Path, modelos) -> int:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with ruta.open("w", encoding="utf-8") as f:
        for m in modelos:
            f.write(m.model_dump_json() + "\n")
            n += 1
    return n


def leer_jsonl(ruta: Path, modelo):
    with ruta.open(encoding="utf-8") as f:
        return [modelo.model_validate(json.loads(l)) for l in f if l.strip()]
