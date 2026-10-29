"""Configuracion y cliente Supabase con service role (solo servidor: nunca exponer esta clave al navegador)."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

BUCKET_ARCHIVOS = "archivos-clientes"
BUCKET_INFORMES = "informes"


def env(nombre: str, defecto: str | None = None) -> str:
    load_dotenv()
    valor = os.getenv(nombre, defecto)
    if valor is None:
        raise RuntimeError(f"Falta la variable de entorno {nombre}")
    return valor


@lru_cache(maxsize=1)
def servicio():
    from supabase import create_client
    return create_client(env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"))
