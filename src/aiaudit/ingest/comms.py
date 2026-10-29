"""Comunicaciones: correos (.eml/.mbox/.msg) y chats exportados de WhatsApp."""
from __future__ import annotations

import email
import mailbox
import re
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd

# Linea tipica de export de WhatsApp: "12/03/24, 09:15 - Juan: texto" o "[12/03/24 09:15:01] Juan: texto"
PATRON_WA = re.compile(r"^\[?(\d{1,2}/\d{1,2}/\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:[ap]\.?\s?m\.?)?\]?\s*-?\s*([^:]+):\s(.*)$", re.I)


def es_whatsapp(ruta: Path) -> bool:
    with ruta.open(encoding="utf-8", errors="replace") as f:
        lineas = [f.readline() for _ in range(10)]
    return sum(bool(PATRON_WA.match(l.strip("‎"))) for l in lineas) >= 3


def _cuerpo(msg) -> str:
    parte = msg.get_body(preferencelist=("plain", "html"))
    if parte is None:
        return ""
    texto = parte.get_content()
    if parte.get_content_type() == "text/html":
        texto = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _fecha(valor) -> str | None:
    try:
        return parsedate_to_datetime(valor).isoformat() if valor else None
    except (TypeError, ValueError):
        return None


def _a_registro(msg) -> dict:
    return {
        "asunto": str(msg.get("subject", "")),
        "de": str(msg.get("from", "")),
        "para": str(msg.get("to", "")),
        "fecha": _fecha(msg.get("date")),
        "cuerpo": _cuerpo(msg),
        "adjuntos": [p.get_filename() for p in msg.iter_attachments() if p.get_filename()],
    }


def leer_correos(ruta: Path) -> list[dict]:
    ext = ruta.suffix.lower()
    if ext == ".eml":
        with ruta.open("rb") as f:
            return [_a_registro(email.message_from_binary_file(f, policy=policy.default))]
    if ext == ".mbox":
        caja = mailbox.mbox(str(ruta), factory=lambda f: email.message_from_binary_file(f, policy=policy.default))
        return [_a_registro(m) for m in caja]
    if ext == ".msg":
        import extract_msg
        with extract_msg.Message(str(ruta)) as m:
            return [{
                "asunto": m.subject or "", "de": m.sender or "", "para": m.to or "",
                "fecha": m.date.isoformat() if m.date else None, "cuerpo": (m.body or "").strip(),
                "adjuntos": [a.longFilename for a in m.attachments if getattr(a, "longFilename", None)],
            }]
    raise ValueError(f"Formato de correo no soportado: {ext}")


def correo_a_texto(r: dict) -> str:
    adj = f"\nAdjuntos: {', '.join(r['adjuntos'])}" if r["adjuntos"] else ""
    return f"Asunto: {r['asunto']}\nDe: {r['de']}\nPara: {r['para']}\nFecha: {r['fecha']}{adj}\n\n{r['cuerpo']}"


def leer_whatsapp(ruta: Path) -> pd.DataFrame:
    """DataFrame date/username/message. Usa whatstk y cae a un parser regex si falla."""
    try:
        from whatstk import WhatsAppChat
        df = WhatsAppChat.from_source(filepath=str(ruta)).df
        if "date" not in df.columns:  # segun la version, la fecha viene como indice
            df = df.reset_index().rename(columns={df.index.name or "index": "date"})
        return df[["date", "username", "message"]]
    except Exception:
        filas = []
        for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
            if m := PATRON_WA.match(linea.strip("‎")):
                filas.append({"date": f"{m[1]} {m[2]}", "username": m[3].strip(), "message": m[4]})
            elif filas:  # mensaje multilinea
                filas[-1]["message"] += "\n" + linea
        df = pd.DataFrame(filas, columns=["date", "username", "message"])
        df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True, format="mixed")
        return df


def whatsapp_por_dia(df: pd.DataFrame) -> list[tuple[str, str]]:
    """Agrupa la conversacion por dia: [(fecha, texto)]."""
    df = df.dropna(subset=["date"])
    salida = []
    for dia, g in df.groupby(df["date"].dt.date):
        lineas = [f"{r.date:%H:%M} {r.username}: {r.message}" for r in g.itertuples()]
        salida.append((str(dia), "\n".join(lineas)))
    return salida
