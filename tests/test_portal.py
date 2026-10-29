"""API del portal y worker de punta a punta contra un Supabase falso en memoria (pipeline en modo offline)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiaudit.portal import app as portal_app
from aiaudit.portal import supa
from aiaudit.portal.worker import Worker, cuestionario_yaml, nombre_seguro
from fake_supabase import FakeSupabase

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def entorno(monkeypatch):
    sb = FakeSupabase()
    monkeypatch.setattr(portal_app, "servicio", lambda: sb)
    for k, v in {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_ANON_KEY": "publica",
                 "SUPABASE_SERVICE_ROLE_KEY": "SECRETA", "PUBLIC_URL": "https://portal.test"}.items():
        monkeypatch.setenv(k, v)
    sb.crear_usuario("admin@consultora.cl", rol="admin")
    sb.crear_usuario("cliente@empresa.cl")
    return sb, TestClient(portal_app.app)


def h(email):
    return {"Authorization": f"Bearer tok-{email}"}


def test_config_no_expone_service_role(entorno):
    _, c = entorno
    js = c.get("/config.js").text
    assert "publica" in js and "SECRETA" not in js


def test_paginas_y_salud(entorno):
    _, c = entorno
    for p in ("/login", "/portal", "/admin"):
        assert c.get(p).status_code == 200
    assert c.get("/api/salud").json() == {"ok": True}


def test_solo_admin(entorno):
    _, c = entorno
    body = {"empresa": "X", "email_cliente": "a@b.cl"}
    assert c.post("/api/admin/proyectos", json=body).status_code == 401
    assert c.post("/api/admin/proyectos", json=body, headers={"Authorization": "Bearer malo"}).status_code == 401
    assert c.post("/api/admin/proyectos", json=body, headers=h("cliente@empresa.cl")).status_code == 403


def test_crear_proyecto_invita_y_reutiliza_usuarios(entorno):
    sb, c = entorno
    r = c.post("/api/admin/proyectos", json={"empresa": "Nueva", "email_cliente": "nuevo@x.cl"}, headers=h("admin@consultora.cl")).json()
    assert r["invitado"] and sb.invitados == ["nuevo@x.cl"]
    r2 = c.post("/api/admin/proyectos", json={"empresa": "Otra", "email_cliente": "cliente@empresa.cl"}, headers=h("admin@consultora.cl")).json()
    assert not r2["invitado"] and len(sb.db["miembros"]) == 2


def test_procesar_valida_archivos_y_duplicados(entorno):
    sb, c = entorno
    pid = c.post("/api/admin/proyectos", json={"empresa": "E", "email_cliente": "cliente@empresa.cl"},
                 headers=h("admin@consultora.cl")).json()["proyecto"]["id"]
    url = f"/api/admin/proyectos/{pid}/procesar"
    assert c.post(url, json={"proveedor_llm": "offline"}, headers=h("admin@consultora.cl")).status_code == 400
    sb.table("archivos").insert({"proyecto_id": pid, "ruta": f"{pid}/a.txt", "nombre": "a.txt"}).execute()
    assert c.post(url, json={"proveedor_llm": "offline"}, headers=h("admin@consultora.cl")).status_code == 200
    assert c.post(url, json={"proveedor_llm": "offline"}, headers=h("admin@consultora.cl")).status_code == 409


def _demo(base: Path):
    spec = importlib.util.spec_from_file_location("crear_demo", RAIZ / "scripts" / "crear_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.crear(base)


def test_worker_de_punta_a_punta(entorno, tmp_path):
    sb, c = entorno
    adm = h("admin@consultora.cl")
    pid = c.post("/api/admin/proyectos", json={"empresa": "Constructora Demo", "sector": "construccion",
                                               "email_cliente": "cliente@empresa.cl"}, headers=adm).json()["proyecto"]["id"]
    # simula lo que hace el navegador del cliente: sube archivos a Storage + filas en 'archivos' + cuestionario
    _demo(tmp_path / "demo")
    for f in sorted((tmp_path / "demo" / "raw").rglob("*")):
        if f.is_file():
            ruta = f"{pid}/x-{f.name}"
            sb.storage.from_(supa.BUCKET_ARCHIVOS).upload(ruta, f.read_bytes())
            sb.table("archivos").insert({"proyecto_id": pid, "ruta": ruta, "nombre": f.name, "tamano": f.stat().st_size}).execute()
    import yaml
    datos = yaml.safe_load((tmp_path / "demo" / "cuestionario.yaml").read_text())
    sb.table("cuestionarios").upsert({"proyecto_id": pid, "datos": datos}).execute()
    sb.table("proyectos").update({"estado": "enviado"}).eq("id", pid).execute()

    assert c.post(f"/api/admin/proyectos/{pid}/procesar", json={"proveedor_llm": "offline"}, headers=adm).status_code == 200
    w = Worker(sb=sb, data_dir=tmp_path / "data")
    assert w.una_vuelta() and not w.una_vuelta()

    trabajo = sb.db["trabajos"][0]
    assert trabajo["estado"] == "ok", trabajo["mensaje"]
    assert sb.db["proyectos"][0]["estado"] == "analizado"
    res = sb.db["resultados"][0]
    assert res["diagnostico"]["oportunidades"] and len(res["informes"]) == 4
    assert all(r in sb.store[supa.BUCKET_INFORMES] for r in res["informes"])
    assert not (tmp_path / "data" / pid / "raw").exists(), "los crudos no deben quedar en el disco del VPS"
    assert sum(o["datos_validados"] for o in res["diagnostico"]["oportunidades"]) == 3

    inf = c.get(f"/api/admin/proyectos/{pid}/informes", headers=adm).json()
    assert {i["nombre"] for i in inf} >= {"01_diagnostico.docx", "02_ruta_integracion_ia.docx"}
    assert c.post(f"/api/admin/proyectos/{pid}/estado/entregado", headers=adm).json()["estado"] == "entregado"


def test_worker_registra_error_y_devuelve_proyecto(entorno, tmp_path):
    sb, _ = entorno
    p = sb.table("proyectos").insert({"empresa": "Vacia"}).execute().data[0]
    sb.table("trabajos").insert({"proyecto_id": p["id"], "proveedor_llm": "offline"}).execute()
    Worker(sb=sb, data_dir=tmp_path).una_vuelta()
    assert sb.db["trabajos"][0]["estado"] == "error" and "no tiene archivos" in sb.db["trabajos"][0]["mensaje"]
    assert sb.db["proyectos"][0]["estado"] == "enviado"


def test_purga_respeta_retencion(entorno, tmp_path):
    sb, _ = entorno
    viejo = sb.table("proyectos").insert({"empresa": "V", "estado": "entregado", "retencion_dias": 30,
                                          "updated_at": "2020-01-01T00:00:00+00:00"}).execute().data[0]
    nuevo = sb.table("proyectos").insert({"empresa": "N", "estado": "analizado"}).execute().data[0]
    for p in (viejo, nuevo):
        sb.storage.from_(supa.BUCKET_ARCHIVOS).upload(f"{p['id']}/a", b"x")
        sb.table("archivos").insert({"proyecto_id": p["id"], "ruta": f"{p['id']}/a", "nombre": "a"}).execute()
    assert Worker(sb=sb, data_dir=tmp_path).purgar_vencidos() == 1
    assert list(sb.store[supa.BUCKET_ARCHIVOS]) == [f"{nuevo['id']}/a"]


def test_utilidades():
    usados = set()
    assert nombre_seguro("../../etc/passwd", usados) == "passwd"
    assert nombre_seguro("passwd", usados) == "passwd_1"
    y = cuestionario_yaml({"empresa": "ACME", "sector": "pyme"}, {"empresa": {"empleados": 5}, "procesos": []})
    assert "nombre: ACME" in y and "empleados: 5" in y
