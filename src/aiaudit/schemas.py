"""Contratos de datos entre etapas. Cada etapa lee y escribe estos modelos en disco."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TipoFuente = Literal["documento", "tabla", "correo", "chat", "audio", "imagen", "otro"]


class Fragmento(BaseModel):
    """Unidad minima de texto extraido de un archivo del cliente."""
    id: str
    origen: str                      # ruta relativa a raw/
    tipo_fuente: TipoFuente
    texto: str
    fecha: str | None = None
    autor: str | None = None
    metadatos: dict = Field(default_factory=dict)


# --- Salida del LLM (esquema estricto: sin dicts ni defaults, requerido por Structured Outputs) ---

class PasoLLM(BaseModel):
    orden: int
    descripcion: str
    actor: str
    herramienta: str
    manual: bool


class ProcesoLLM(BaseModel):
    id_taxonomia: str                # id de la taxonomia o "" si es un proceso nuevo
    nombre: str
    area: str
    descripcion: str
    actores: list[str]
    herramientas: list[str]
    pasos: list[PasoLLM]
    frecuencia: str                  # diaria | semanal | mensual | por_proyecto | desconocida
    dolores: list[str]               # problemas, demoras, retrabajos detectados
    evidencias: list[str]            # ids de fragmentos que respaldan el proceso
    confianza: float                 # 0-1


class ExtraccionLLM(BaseModel):
    procesos: list[ProcesoLLM]


# --- Modelo consolidado ---

class Proceso(BaseModel):
    id: str
    nombre: str
    area: str = ""
    descripcion: str = ""
    actores: list[str] = Field(default_factory=list)
    herramientas: list[str] = Field(default_factory=list)
    pasos: list[PasoLLM] = Field(default_factory=list)
    frecuencia: str = "desconocida"
    dolores: list[str] = Field(default_factory=list)
    evidencias: list[str] = Field(default_factory=list)
    fuentes_deteccion: list[str] = Field(default_factory=list)  # llm | offline | mining | cuestionario
    confianza: float = 0.0
    # datos del cuestionario (None = no validado con el cliente)
    horas_mes: float | None = None
    personas: int | None = None
    costo_hora: float | None = None
    dolor: int | None = None
    diagrama_mermaid: str = ""


class Oportunidad(BaseModel):
    proceso_id: str
    proceso_nombre: str
    area: str
    solucion: str
    tipo_solucion: str
    herramientas_sugeridas: list[str]
    horas_mes: float
    automatizable: float
    horas_ahorradas_mes: float
    ahorro_mensual: float
    complejidad: int
    puntaje: float
    fase: str
    datos_validados: bool
    supuestos: list[str] = Field(default_factory=list)


class Diagnostico(BaseModel):
    cliente: str
    sector: str
    procesos: list[Proceso]
    oportunidades: list[Oportunidad] = Field(default_factory=list)
    resumen_fuentes: dict = Field(default_factory=dict)
