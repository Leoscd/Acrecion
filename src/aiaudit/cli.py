"""CLI: cada etapa se puede ejecutar sola o todas con 'run'.

aiaudit nuevo <cliente>          crea carpetas y copia el cuestionario
aiaudit ingest <cliente>         raw/ -> interim/fragmentos.jsonl
aiaudit anonymize <cliente>      -> interim/fragmentos_anon.jsonl
aiaudit analyze <cliente>        -> processed/diagnostico.json (LLM + mining + cuestionario)
aiaudit score <cliente>          -> oportunidades priorizadas
aiaudit report <cliente>         -> outputs/*.docx
aiaudit run <cliente>            todo lo anterior
"""
from __future__ import annotations

import json
import logging

import typer
from rich.console import Console
from rich.logging import RichHandler

from aiaudit.config import CONFIG_DIR, Cliente

app = typer.Typer(add_completion=False, help="Diagnostico de procesos e integracion de IA para empresas.")
consola = Console()


@app.callback()
def _config(verbose: bool = typer.Option(False, "--verbose", "-v")):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s",
                        handlers=[RichHandler(console=consola, show_path=False)])
    for ruidoso in ("httpx", "openai", "presidio-analyzer", "docling", "faster_whisper", "RapidOCR", "pm4py"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def _cliente(nombre: str, proveedor: str | None) -> Cliente:
    """Proveedor desde la CLI sobrescribe settings.yaml sin modificarlo."""
    return Cliente.desde_nombre(nombre, overrides={"llm": {"provider": proveedor}} if proveedor else None)


def _mostrar(etapa: str, resultado: dict):
    consola.print(f"[bold green]{etapa}[/] " + json.dumps(resultado, ensure_ascii=False))


Proveedor = typer.Option(None, "--llm", help="openai | ollama | offline (sobrescribe settings.yaml)")


@app.command()
def nuevo(cliente: str, sector: str = typer.Option("pyme", help="construccion | pyme")):
    """Crea la estructura del cliente y su cuestionario."""
    c = Cliente.desde_nombre(cliente)
    destino = c.base / "cuestionario.yaml"
    if not destino.exists():
        texto = (CONFIG_DIR / "cuestionario.yaml").read_text(encoding="utf-8").replace("sector: pyme ", f"sector: {sector} ", 1)
        destino.write_text(texto, encoding="utf-8")
    consola.print(f"Cliente creado en {c.base}\n1) Copiar archivos a {c.raw}\n2) Completar {destino}\n3) aiaudit run {cliente}")


@app.command()
def ingest(cliente: str):
    """Extrae texto de todos los archivos de raw/."""
    from aiaudit.ingest.router import ingerir
    _mostrar("ingest", ingerir(Cliente.desde_nombre(cliente)))


@app.command()
def anonymize(cliente: str):
    """Reemplaza datos personales por tokens antes de usar el LLM."""
    from aiaudit.privacy.anonymize import anonimizar
    _mostrar("anonymize", anonimizar(Cliente.desde_nombre(cliente)))


@app.command()
def analyze(cliente: str, llm: str = Proveedor):
    """Detecta procesos (LLM u offline), mina logs de eventos y cruza con el cuestionario."""
    from aiaudit.analyze import consolidate, process_extract, process_mining
    c = _cliente(cliente, llm)
    _mostrar("extraccion", process_extract.extraer(c))
    _mostrar("mining", process_mining.minar(c))
    _mostrar("consolidacion", consolidate.consolidar(c))


@app.command()
def score(cliente: str, llm: str = Proveedor):
    """Prioriza oportunidades de IA por ahorro, dolor, confianza y complejidad."""
    from aiaudit.score.opportunity import priorizar
    _mostrar("score", priorizar(_cliente(cliente, llm)))


@app.command()
def report(cliente: str, llm: str = Proveedor):
    """Genera diagnostico, ruta de integracion y plan de capacitacion."""
    from aiaudit.report import generar
    _mostrar("report", generar(_cliente(cliente, llm)))


@app.command()
def run(cliente: str, llm: str = Proveedor, desde: str = typer.Option("ingest", help="ingest | anonymize | analyze | score | report")):
    """Pipeline completo. --desde permite reanudar sin reprocesar etapas previas."""
    etapas = ["ingest", "anonymize", "analyze", "score", "report"]
    if desde not in etapas:
        raise typer.BadParameter(f"--desde debe ser una de {etapas}")
    c = _cliente(cliente, llm)
    for etapa in etapas[etapas.index(desde):]:
        if etapa == "ingest":
            ingest(cliente)
        elif etapa == "anonymize":
            if c.settings["privacidad"]["anonimizar"]:
                anonymize(cliente)
        else:
            {"analyze": analyze, "score": score, "report": report}[etapa](cliente, llm)
    consola.print(f"[bold]Entregables en {c.outputs}[/]")


if __name__ == "__main__":
    app()
