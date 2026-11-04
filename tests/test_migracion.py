"""Prueba la migracion y sus politicas RLS en un Postgres local efimero (se omite si no hay binarios)."""
from __future__ import annotations

import glob
import os
import shutil
import tempfile
import subprocess
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
BIN = next(iter(sorted(glob.glob("/usr/lib/postgresql/*/bin"))), None) or (
    str(Path(shutil.which("pg_ctl")).parent) if shutil.which("pg_ctl") else None)
pytestmark = pytest.mark.skipif(not BIN, reason="Postgres local no disponible")

ADMIN, CLIENTE, OTRO = (str(uuid.uuid4()) for _ in range(3))


# initdb no corre como root: en ese caso se usa el usuario del sistema 'postgres'
COMO = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []


@pytest.fixture(scope="module")
def psql():
    datos = Path(tempfile.mkdtemp(prefix="pg-aiaudit-"))
    if COMO:
        shutil.chown(datos, "postgres")
    puerto = "54329"
    subprocess.run(COMO + [f"{BIN}/initdb", "-D", str(datos), "-U", "postgres", "-A", "trust"], check=True, capture_output=True)
    subprocess.run(COMO + [f"{BIN}/pg_ctl", "-D", str(datos), "-o", f"-p {puerto} -k {datos}", "-l", str(datos / "log"), "-w", "start"],
                   check=True, capture_output=True)

    def ejecutar(sql: str, rol: str | None = None, usuario: str | None = None) -> str:
        prefijo = ""
        if rol:
            prefijo = f"set role {rol}; select set_config('request.jwt.claim.sub', '{usuario or ''}', false);"
        r = subprocess.run(["psql", "-h", str(datos), "-p", puerto, "-U", "postgres", "-v", "ON_ERROR_STOP=1",
                            "-qtA", "-c", prefijo + sql], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr.strip())
        return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""

    ejecutar((RAIZ / "tests/sql/stub_supabase.sql").read_text())
    for m in sorted((RAIZ / "supabase/migrations").glob("*.sql")):
        ejecutar(m.read_text())
    ejecutar(f"insert into auth.users values ('{ADMIN}', 'admin@x.cl'), ('{CLIENTE}', 'c@x.cl'), ('{OTRO}', 'o@x.cl');"
             f"update public.perfiles set rol = 'admin' where id = '{ADMIN}';")
    yield ejecutar
    subprocess.run(COMO + [f"{BIN}/pg_ctl", "-D", str(datos), "-m", "immediate", "stop"], capture_output=True)
    shutil.rmtree(datos, ignore_errors=True)


def test_trigger_crea_perfiles(psql):
    assert psql("select count(*) from public.perfiles") == "3"


def test_flujo_rls(psql):
    p = psql("insert into public.proyectos (empresa) values ('Demo') returning id", "authenticated", ADMIN)
    psql(f"insert into public.miembros values ('{p}', '{CLIENTE}')", "authenticated", ADMIN)

    # el cliente ve su proyecto, el otro usuario no
    assert psql("select count(*) from public.proyectos", "authenticated", CLIENTE) == "1"
    assert psql("select count(*) from public.proyectos", "authenticated", OTRO) == "0"

    # el cliente sube archivo y cuestionario; el otro no puede
    psql(f"insert into public.archivos (proyecto_id, ruta, nombre) values ('{p}', '{p}/a.pdf', 'a.pdf')", "authenticated", CLIENTE)
    psql(f"insert into storage.objects (bucket_id, name, owner_id) values ('archivos-clientes', '{p}/a.pdf', '{CLIENTE}')",
         "authenticated", CLIENTE)
    psql(f"insert into public.cuestionarios (proyecto_id, datos) values ('{p}', '{{}}')", "authenticated", CLIENTE)
    with pytest.raises(RuntimeError, match="row-level security"):
        psql(f"insert into public.archivos (proyecto_id, ruta, nombre) values ('{p}', '{p}/b.pdf', 'b')", "authenticated", OTRO)
    with pytest.raises(RuntimeError, match="row-level security"):
        psql(f"insert into storage.objects (bucket_id, name, owner_id) values ('archivos-clientes', '{p}/x.pdf', '{OTRO}')",
             "authenticated", OTRO)
    # ruta fuera de su carpeta de proyecto
    with pytest.raises(RuntimeError, match="row-level security"):
        psql(f"insert into public.archivos (proyecto_id, ruta, nombre) values ('{p}', 'otro/c.pdf', 'c')", "authenticated", CLIENTE)

    # el cliente no puede escalar a admin ni ver resultados/trabajos
    psql(f"update public.perfiles set rol = 'admin' where id = '{CLIENTE}'", "authenticated", CLIENTE)
    assert psql(f"select rol from public.perfiles where id = '{CLIENTE}'") == "cliente"
    psql(f"insert into public.trabajos (proyecto_id) values ('{p}')", "authenticated", ADMIN)
    assert psql("select count(*) from public.trabajos", "authenticated", CLIENTE) == "0"
    with pytest.raises(RuntimeError, match="permission denied"):
        psql("select * from public.tomar_trabajo()", "authenticated", CLIENTE)

    # al enviar, el proyecto se cierra: ya no se puede subir ni editar
    psql(f"select public.enviar_proyecto('{p}')", "authenticated", CLIENTE)
    assert psql(f"select estado from public.proyectos where id = '{p}'") == "enviado"
    with pytest.raises(RuntimeError, match="row-level security"):
        psql(f"insert into public.archivos (proyecto_id, ruta, nombre) values ('{p}', '{p}/d.pdf', 'd')", "authenticated", CLIENTE)
    psql(f"update public.cuestionarios set datos = '{{\"x\": 1}}' where proyecto_id = '{p}'", "authenticated", CLIENTE)
    assert psql(f"select datos::text from public.cuestionarios where proyecto_id = '{p}'") == "{}"

    # la cola entrega cada trabajo una sola vez
    assert psql("select count(*) from public.tomar_trabajo()", "service_role") == "1"
    assert psql("select count(*) from public.tomar_trabajo()", "service_role") == "0"
