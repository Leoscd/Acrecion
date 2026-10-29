"""Worker: toma trabajos de la cola en Supabase, corre el pipeline y sube los informes.

Uso: python -m aiaudit.portal.worker
Flujo por trabajo: descargar archivos -> escribir cuestionario.yaml -> pipeline.ejecutar -> subir informes
-> guardar diagnostico en 'resultados' -> borrar los archivos crudos del disco local.
Cada hora purga de Storage los archivos de proyectos analizados que superaron su retencion.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from aiaudit.config import Cliente
from aiaudit.pipeline import ejecutar
from aiaudit.portal.supa import BUCKET_ARCHIVOS, BUCKET_INFORMES, env, servicio

log = logging.getLogger("aiaudit.worker")
MIME = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".md": "text/markdown"}


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def nombre_seguro(nombre: str, usados: set[str]) -> str:
    """Evita rutas relativas y colisiones de nombres al descargar."""
    base = re.sub(r"[^\w.\- ]", "_", Path(nombre).name).strip() or "archivo"
    candidato, n = base, 1
    while candidato.lower() in usados:
        candidato = f"{Path(base).stem}_{n}{Path(base).suffix}"
        n += 1
    usados.add(candidato.lower())
    return candidato


def cuestionario_yaml(proyecto: dict, datos: dict | None) -> str:
    """El cuestionario web tiene la misma estructura que config/cuestionario.yaml; empresa y sector vienen del proyecto."""
    datos = dict(datos or {})
    empresa = {**(datos.get("empresa") or {}), "nombre": proyecto["empresa"], "sector": proyecto["sector"]}
    return yaml.safe_dump({**datos, "empresa": empresa}, allow_unicode=True, sort_keys=False)


class Worker:
    def __init__(self, sb=None, data_dir: Path | None = None):
        self.sb = sb or servicio()
        self.data_dir = data_dir or Path(env("AIAUDIT_DATA_DIR", str(Path.cwd() / "data")))

    def _trabajo(self, id_: str, **campos):
        self.sb.table("trabajos").update(campos).eq("id", id_).execute()

    def _proyecto(self, id_: str, **campos):
        self.sb.table("proyectos").update(campos).eq("id", id_).execute()

    def descargar(self, proyecto_id: str, cliente: Cliente) -> int:
        shutil.rmtree(cliente.raw, ignore_errors=True)
        cliente.raw.mkdir(parents=True)
        archivos = self.sb.table("archivos").select("ruta,nombre").eq("proyecto_id", proyecto_id).execute().data
        usados: set[str] = set()
        for a in archivos:
            contenido = self.sb.storage.from_(BUCKET_ARCHIVOS).download(a["ruta"])
            (cliente.raw / nombre_seguro(a["nombre"], usados)).write_bytes(contenido)
        return len(archivos)

    def subir_informes(self, proyecto_id: str, cliente: Cliente) -> list[str]:
        rutas = []
        for f in sorted(cliente.outputs.iterdir()):
            ruta = f"{proyecto_id}/{f.name}"
            self.sb.storage.from_(BUCKET_INFORMES).upload(
                ruta, f.read_bytes(), {"content-type": MIME.get(f.suffix, "application/octet-stream"), "upsert": "true"})
            rutas.append(ruta)
        return rutas

    def procesar(self, t: dict):
        pid = t["proyecto_id"]
        proyecto = self.sb.table("proyectos").select("*").eq("id", pid).single().execute().data
        self._proyecto(pid, estado="procesando")
        cliente = Cliente.desde_nombre(pid, data_dir=self.data_dir, overrides={"llm": {"provider": t["proveedor_llm"]}})
        try:
            self._trabajo(t["id"], etapa="descarga")
            n = self.descargar(pid, cliente)
            if not n:
                raise RuntimeError("El proyecto no tiene archivos")
            q = self.sb.table("cuestionarios").select("datos").eq("proyecto_id", pid).execute().data
            (cliente.base / "cuestionario.yaml").write_text(cuestionario_yaml(proyecto, q[0]["datos"] if q else None),
                                                           encoding="utf-8")
            shutil.rmtree(cliente.outputs, ignore_errors=True)
            cliente.outputs.mkdir()
            resumen = ejecutar(cliente, al_avanzar=lambda etapa, _: self._trabajo(t["id"], etapa=etapa))
            self._trabajo(t["id"], etapa="subida")
            informes = self.subir_informes(pid, cliente)
            self.sb.table("resultados").upsert({
                "proyecto_id": pid, "diagnostico": json.loads(cliente.diagnostico.read_text(encoding="utf-8")),
                "informes": informes, "resumen": resumen, "updated_at": _ahora()}).execute()
            self._trabajo(t["id"], estado="ok", etapa="fin", terminado_at=_ahora(), mensaje=f"{n} archivos procesados")
            self._proyecto(pid, estado="analizado")
            log.info("Trabajo %s ok (%d archivos)", t["id"], n)
        except Exception as e:
            log.exception("Trabajo %s fallo", t["id"])
            self._trabajo(t["id"], estado="error", terminado_at=_ahora(), mensaje=f"{type(e).__name__}: {e}"[:1000])
            self._proyecto(pid, estado="enviado")
        finally:
            shutil.rmtree(cliente.raw, ignore_errors=True)  # los crudos solo viven en Storage

    def purgar_vencidos(self) -> int:
        """Borra de Storage los archivos crudos de proyectos analizados/entregados con retencion vencida."""
        proyectos = self.sb.table("proyectos").select("id,retencion_dias,updated_at") \
            .in_("estado", ["analizado", "entregado"]).execute().data
        borrados = 0
        for p in proyectos:
            limite = datetime.fromisoformat(p["updated_at"]) + timedelta(days=p["retencion_dias"])
            if limite > datetime.now(timezone.utc):
                continue
            rutas = [a["ruta"] for a in self.sb.table("archivos").select("ruta").eq("proyecto_id", p["id"]).execute().data]
            if rutas:
                self.sb.storage.from_(BUCKET_ARCHIVOS).remove(rutas)
                self.sb.table("archivos").delete().eq("proyecto_id", p["id"]).execute()
                borrados += len(rutas)
        return borrados

    def una_vuelta(self) -> bool:
        trabajos = self.sb.rpc("tomar_trabajo").execute().data or []
        for t in trabajos:
            self.procesar(t)
        return bool(trabajos)

    def bucle(self, intervalo: float = 10.0):
        log.info("Worker iniciado (data: %s)", self.data_dir)
        ultima_purga = 0.0
        while True:
            try:
                if time.time() - ultima_purga > 3600:
                    if n := self.purgar_vencidos():
                        log.info("Purgados %d archivos vencidos", n)
                    ultima_purga = time.time()
                if not self.una_vuelta():
                    time.sleep(intervalo)
            except Exception:  # errores de red: se reintenta en la siguiente vuelta
                log.exception("Error en el bucle del worker")
                time.sleep(intervalo * 3)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    Worker().bucle(float(env("WORKER_INTERVALO", "10")))
