"""Supabase en memoria con el subconjunto de la API que usan el portal y el worker. RLS no se simula aqui
(eso lo prueba test_migracion.py contra Postgres real); esto prueba la logica del servidor y del worker."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

CLAVES = {"proyectos": ("id",), "trabajos": ("id",), "archivos": ("id",), "perfiles": ("id",),
          "resultados": ("proyecto_id",), "cuestionarios": ("proyecto_id",), "miembros": ("proyecto_id", "usuario_id")}
DEFAULTS = {
    "proyectos": lambda: {"id": str(uuid.uuid4()), "estado": "recibiendo", "sector": "pyme", "retencion_dias": 90,
                          "updated_at": datetime.now(timezone.utc).isoformat()},
    "trabajos": lambda: {"id": str(uuid.uuid4()), "estado": "pendiente", "proveedor_llm": "openai", "etapa": None, "mensaje": None},
    "archivos": lambda: {"id": str(uuid.uuid4())},
}


class Consulta:
    def __init__(self, db, tabla):
        self.db, self.tabla, self.filtros, self.op, self.valores, self.uno, self.tope = db, tabla, [], "select", None, False, None

    def select(self, *_ , **__): return self
    def order(self, *_, **__): return self
    def eq(self, col, v): self.filtros.append(lambda r: r.get(col) == v); return self
    def in_(self, col, vs): self.filtros.append(lambda r: r.get(col) in vs); return self
    def limit(self, n): self.tope = n; return self
    def single(self): self.uno = True; return self
    def insert(self, v): self.op, self.valores = "insert", v; return self
    def upsert(self, v): self.op, self.valores = "upsert", v; return self
    def update(self, v): self.op, self.valores = "update", v; return self
    def delete(self): self.op = "delete"; return self

    def _filas(self):
        return [r for r in self.db.setdefault(self.tabla, []) if all(f(r) for f in self.filtros)]

    def execute(self):
        filas = self.db.setdefault(self.tabla, [])
        if self.op == "insert":
            nuevas = [{**DEFAULTS.get(self.tabla, dict)(), **v} for v in (self.valores if isinstance(self.valores, list) else [self.valores])]
            filas.extend(nuevas)
            return SimpleNamespace(data=nuevas)
        if self.op == "upsert":
            v, clave = self.valores, CLAVES[self.tabla]
            existente = next((r for r in filas if all(r.get(k) == v.get(k) for k in clave)), None)
            if existente:
                existente.update(v)
            else:
                filas.append({**DEFAULTS.get(self.tabla, dict)(), **v})
            return SimpleNamespace(data=[v])
        sel = self._filas()
        if self.op == "update":
            for r in sel:
                r.update(self.valores)
            return SimpleNamespace(data=sel)
        if self.op == "delete":
            self.db[self.tabla] = [r for r in filas if r not in sel]
            return SimpleNamespace(data=sel)
        sel = sel[: self.tope] if self.tope else sel
        return SimpleNamespace(data=(sel[0] if sel else None) if self.uno else [dict(r) for r in sel])


class Bucket:
    def __init__(self, store, nombre):
        self.archivos = store.setdefault(nombre, {})

    def upload(self, ruta, contenido, opciones=None): self.archivos[ruta] = contenido; return {"path": ruta}
    def download(self, ruta): return self.archivos[ruta]
    def remove(self, rutas): [self.archivos.pop(r, None) for r in rutas]; return []
    def create_signed_url(self, ruta, segundos): return {"signedURL": f"https://firmado/{ruta}?t={segundos}"}


class FakeSupabase:
    def __init__(self):
        self.db: dict[str, list[dict]] = {}
        self.store: dict[str, dict[str, bytes]] = {}
        self.tokens: dict[str, str] = {}   # token -> user id
        self.invitados: list[str] = []
        sb = self
        self.storage = SimpleNamespace(from_=lambda b: Bucket(sb.store, b))
        self.auth = SimpleNamespace(get_user=self._get_user, admin=SimpleNamespace(invite_user_by_email=self._invitar))

    def table(self, nombre): return Consulta(self.db, nombre)

    def rpc(self, nombre, params=None):
        assert nombre == "tomar_trabajo"
        pend = [t for t in self.db.get("trabajos", []) if t["estado"] == "pendiente"]
        if pend:
            pend[0].update(estado="ejecutando", etapa="inicio")
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[dict(pend[0])] if pend else []))

    def crear_usuario(self, email, rol="cliente") -> str:
        uid = str(uuid.uuid4())
        self.db.setdefault("perfiles", []).append({"id": uid, "email": email, "rol": rol})
        self.tokens[f"tok-{email}"] = uid
        return uid

    def _get_user(self, token):
        if token not in self.tokens:
            raise ValueError("token invalido")
        uid = self.tokens[token]
        email = next(p["email"] for p in self.db["perfiles"] if p["id"] == uid)
        return SimpleNamespace(user=SimpleNamespace(id=uid, email=email))

    def _invitar(self, email, opciones=None):
        self.invitados.append(email)
        return SimpleNamespace(user=SimpleNamespace(id=self.crear_usuario(email)))
