"""API y paginas del portal.

Las lecturas/escrituras normales las hace el navegador con supabase-js, protegidas por RLS.
Esta API solo cubre lo que necesita la service role: invitar clientes, encolar trabajos,
cambiar el estado de un proyecto y firmar descargas de informes.

Uso: uvicorn aiaudit.portal.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from aiaudit.portal.supa import BUCKET_INFORMES, env, servicio

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="aiaudit portal", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------- Autenticacion ----------

def usuario(authorization: str = Header(default="")) -> dict:
    """Valida el JWT de Supabase enviado por el navegador y devuelve {id, email, rol}."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Falta token")
    sb = servicio()
    try:
        u = sb.auth.get_user(token).user
    except Exception:
        raise HTTPException(401, "Sesion invalida o vencida")
    perfil = sb.table("perfiles").select("rol").eq("id", u.id).execute().data
    return {"id": u.id, "email": u.email, "rol": perfil[0]["rol"] if perfil else "cliente"}


def admin(u: dict = Depends(usuario)) -> dict:
    if u["rol"] != "admin":
        raise HTTPException(403, "Solo administradores")
    return u


# ---------- Paginas ----------

@app.get("/", include_in_schema=False)
def inicio():
    return RedirectResponse("/portal")


for _pagina in ("login", "portal", "admin"):
    app.add_api_route(f"/{_pagina}", (lambda p=_pagina: FileResponse(STATIC / f"{p}.html")), include_in_schema=False)


@app.get("/config.js", include_in_schema=False)
def config_js():
    """Solo datos publicos: URL del proyecto y clave publicable (anon). La service role nunca sale del servidor."""
    cfg = {"supabaseUrl": env("SUPABASE_URL"), "supabaseKey": env("SUPABASE_ANON_KEY"),
           "maxMb": int(env("MAX_ARCHIVO_MB", "50"))}
    return Response(f"window.AIAUDIT = {json.dumps(cfg)};", media_type="application/javascript")


@app.get("/api/salud")
def salud():
    return {"ok": True}


@app.get("/api/yo")
def yo(u: dict = Depends(usuario)):
    return u


# ---------- Administracion ----------

class NuevoProyecto(BaseModel):
    empresa: str
    sector: Literal["construccion", "pyme"] = "pyme"
    email_cliente: EmailStr
    retencion_dias: int = 90


class Invitacion(BaseModel):
    email: EmailStr


class Procesar(BaseModel):
    proveedor_llm: Literal["openai", "ollama", "offline"] = "openai"


def _invitar(proyecto_id: str, email: str) -> dict:
    """Agrega al usuario al proyecto; si no existe, Supabase le envia un correo de invitacion con enlace magico."""
    sb = servicio()
    existente = sb.table("perfiles").select("id").eq("email", email.lower()).execute().data
    if existente:
        uid, invitado = existente[0]["id"], False
    else:
        r = sb.auth.admin.invite_user_by_email(email.lower(), {"redirect_to": f"{env('PUBLIC_URL')}/portal"})
        uid, invitado = r.user.id, True
    sb.table("miembros").upsert({"proyecto_id": proyecto_id, "usuario_id": uid}).execute()
    return {"usuario_id": uid, "invitado": invitado}


def _proyecto(pid: str) -> dict:
    datos = servicio().table("proyectos").select("*").eq("id", pid).execute().data
    if not datos:
        raise HTTPException(404, "Proyecto no encontrado")
    return datos[0]


@app.post("/api/admin/proyectos")
def crear_proyecto(body: NuevoProyecto, u: dict = Depends(admin)):
    p = servicio().table("proyectos").insert({
        "empresa": body.empresa, "sector": body.sector, "retencion_dias": body.retencion_dias,
        "creado_por": u["id"]}).execute().data[0]
    return {"proyecto": p, **_invitar(p["id"], body.email_cliente)}


@app.post("/api/admin/proyectos/{pid}/invitar")
def invitar(pid: str, body: Invitacion, _: dict = Depends(admin)):
    _proyecto(pid)
    return _invitar(pid, body.email)


@app.post("/api/admin/proyectos/{pid}/procesar")
def procesar(pid: str, body: Procesar, u: dict = Depends(admin)):
    _proyecto(pid)
    sb = servicio()
    activos = sb.table("trabajos").select("id").eq("proyecto_id", pid).in_("estado", ["pendiente", "ejecutando"]).execute().data
    if activos:
        raise HTTPException(409, "Ya hay un trabajo en curso para este proyecto")
    if not sb.table("archivos").select("id").eq("proyecto_id", pid).limit(1).execute().data:
        raise HTTPException(400, "El proyecto no tiene archivos")
    return sb.table("trabajos").insert({"proyecto_id": pid, "proveedor_llm": body.proveedor_llm,
                                        "creado_por": u["id"]}).execute().data[0]


@app.post("/api/admin/proyectos/{pid}/estado/{estado}")
def cambiar_estado(pid: str, estado: Literal["recibiendo", "entregado"], _: dict = Depends(admin)):
    """'recibiendo' reabre la carga al cliente; 'entregado' cierra el proyecto."""
    _proyecto(pid)
    return servicio().table("proyectos").update({"estado": estado}).eq("id", pid).execute().data[0]


@app.get("/api/admin/proyectos/{pid}/informes")
def informes(pid: str, _: dict = Depends(admin)):
    """URLs firmadas (10 min) para descargar los informes generados."""
    sb = servicio()
    res = sb.table("resultados").select("informes").eq("proyecto_id", pid).execute().data
    if not res:
        return []
    salida = []
    for ruta in res[0]["informes"]:
        firmada = sb.storage.from_(BUCKET_INFORMES).create_signed_url(ruta, 600)
        salida.append({"nombre": ruta.split("/")[-1], "url": firmada.get("signedURL") or firmada.get("signedUrl")})
    return salida
