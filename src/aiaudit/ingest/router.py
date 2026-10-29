"""Recorre raw/, enruta cada archivo a su extractor y escribe fragmentos.jsonl + inventario.json."""
from __future__ import annotations

import json
import logging
import mimetypes
import zipfile
from pathlib import Path

from aiaudit.config import Cliente, escribir_jsonl
from aiaudit.ingest import comms, documents, media, tabular
from aiaudit.ingest.base import fragmentar, rel, slug
from aiaudit.schemas import Fragmento

log = logging.getLogger(__name__)

EXT = {
    "documento": {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".odt", ".rtf", ".md", ".html", ".htm", ".json", ".xml", ".epub"},
    "tabla": {".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv"},
    "correo": {".eml", ".mbox", ".msg"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".mp4", ".mov", ".webm", ".mkv"},
    "imagen": {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"},
}
IGNORAR = {".ds_store", ".gitkeep", "thumbs.db"}


def clasificar(ruta: Path) -> str:
    ext = ruta.suffix.lower()
    if ext == ".txt":
        return "chat" if comms.es_whatsapp(ruta) else "documento"
    for tipo, exts in EXT.items():
        if ext in exts:
            return tipo
    # extension desconocida: se intenta por tipo MIME (python-magic si esta, si no mimetypes)
    try:
        import magic
        mime = magic.from_file(str(ruta), mime=True)
    except ImportError:
        mime = mimetypes.guess_type(ruta.name)[0] or ""
    if mime.startswith("audio/") or mime.startswith("video/"):
        return "audio"
    if mime.startswith("image/"):
        return "imagen"
    return "documento" if mime.startswith("text/") or mime == "application/pdf" else "desconocido"


def _descomprimir(zip_path: Path, destino: Path) -> Path:
    """Extrae un zip evitando rutas fuera del destino (zip slip)."""
    salida = destino / slug(zip_path.stem)
    with zipfile.ZipFile(zip_path) as z:
        for miembro in z.infolist():
            objetivo = (salida / miembro.filename).resolve()
            if not objetivo.is_relative_to(salida.resolve()):
                raise ValueError(f"Ruta insegura en zip: {miembro.filename}")
        z.extractall(salida)
    return salida


def extraer(ruta: Path, origen: str, tipo: str, cfg: dict, cliente: Cliente) -> list[Fragmento]:
    max_c = cfg["max_chars_fragmento"]
    if tipo == "documento":
        md, motor = documents.a_markdown(ruta, cfg["motor_documentos"])
        return fragmentar(md, origen, "documento", max_c, metadatos={"motor": motor})
    if tipo == "tabla":
        frags = []
        for hoja, df in tabular.leer_hojas(ruta).items():
            frags += fragmentar(tabular.resumir(f"{ruta.name} / {hoja}", df), f"{origen}::{hoja}", "tabla", max_c,
                                metadatos={"filas": len(df), "columnas": [str(c) for c in df.columns]})
            if (ev := tabular.detectar_log_eventos(df)) is not None:
                cliente.tablas_log.mkdir(parents=True, exist_ok=True)
                ev.to_csv(cliente.tablas_log / f"{slug(origen + '-' + hoja)}.csv", index=False)
                frags[-1].metadatos["log_eventos"] = True
        return frags
    if tipo == "correo":
        frags = []
        for r in comms.leer_correos(ruta):
            frags += fragmentar(comms.correo_a_texto(r), origen, "correo", max_c, fecha=r["fecha"], autor=r["de"],
                                metadatos={"asunto": r["asunto"]})
        for i, f in enumerate(frags):  # ids unicos dentro de un mbox
            f.id = f"{slug(origen)}#{i}"
        return frags
    if tipo == "chat":
        df = comms.leer_whatsapp(ruta)
        participantes = sorted(df["username"].dropna().astype(str).unique().tolist())
        frags = []
        for dia, texto in comms.whatsapp_por_dia(df):
            frags += fragmentar(texto, origen, "chat", max_c, fecha=dia, metadatos={"participantes": participantes})
        for i, f in enumerate(frags):
            f.id = f"{slug(origen)}#{i}"
        return frags
    if tipo == "audio":
        texto, meta = media.transcribir(ruta, cfg["whisper_modelo"], cfg["idioma"])
        return fragmentar(texto.replace("\n", "\n\n"), origen, "audio", max_c, metadatos=meta)
    if tipo == "imagen":
        texto, meta = media.ocr_imagen(ruta)
        return fragmentar(texto, origen, "imagen", max_c, metadatos=meta)
    return []


def ingerir(cliente: Cliente) -> dict:
    cfg = cliente.settings["ingesta"]
    pendientes = sorted(p for p in cliente.raw.rglob("*") if p.is_file() and p.name.lower() not in IGNORAR)
    fragmentos, inventario = [], []
    while pendientes:
        ruta = pendientes.pop(0)
        origen = rel(ruta, cliente.raw) if ruta.is_relative_to(cliente.raw) else rel(ruta, cliente.interim)
        if ruta.suffix.lower() == ".zip":
            carpeta = _descomprimir(ruta, cliente.interim / "descomprimidos")
            pendientes += sorted(p for p in carpeta.rglob("*") if p.is_file())
            inventario.append({"archivo": origen, "tipo": "zip", "fragmentos": 0, "error": None})
            continue
        tipo = clasificar(ruta)
        item = {"archivo": origen, "tipo": tipo, "fragmentos": 0, "error": None}
        try:
            frags = extraer(ruta, origen, tipo, cfg, cliente)
            fragmentos += frags
            item["fragmentos"] = len(frags)
            if tipo == "desconocido":
                item["error"] = "formato no soportado"
        except Exception as e:  # un archivo roto no detiene el pipeline
            log.warning("Error procesando %s: %s", origen, e)
            item["error"] = f"{type(e).__name__}: {e}"
        inventario.append(item)
        log.info("%-10s %-50s %d fragmentos", tipo, origen, item["fragmentos"])

    escribir_jsonl(cliente.fragmentos, fragmentos)
    (cliente.interim / "inventario.json").write_text(json.dumps(inventario, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"archivos": len(inventario), "fragmentos": len(fragmentos),
            "errores": sum(1 for i in inventario if i["error"])}
