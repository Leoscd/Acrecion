# aiaudit

Pipeline CLI para diagnosticar los procesos de una empresa a partir de sus archivos y generar:

1. `01_diagnostico.docx` / `.md`: procesos detectados, pasos, dolores y oportunidades priorizadas.
2. `02_ruta_integracion_ia.docx`: ruta de integración por fases, con una ficha técnica de desarrollo por iniciativa.
3. `03_plan_capacitacion.docx`: un módulo de capacitación por solución, adaptado a los roles del cliente.

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
python -m spacy download es_core_news_md   # nombres para la anonimización
cp .env.example .env                        # poner OPENAI_API_KEY
```

Los extras se pueden instalar por separado: `llm`, `docs` (Docling), `comms`, `media` (Whisper/OCR), `mining` (pm4py), `privacy` (Presidio).

## Uso

```bash
aiaudit nuevo cliente_x --sector construccion   # crea data/cliente_x/ y su cuestionario.yaml
# copiar los archivos del cliente a data/cliente_x/raw/ (se aceptan carpetas y .zip)
# completar data/cliente_x/cuestionario.yaml en la entrevista
aiaudit run cliente_x                           # pipeline completo con OpenAI
aiaudit run cliente_x --llm offline             # sin API (palabras clave)
aiaudit run cliente_x --desde score             # reanudar desde una etapa
```

Demo: `python scripts/crear_demo.py && aiaudit run demo_constructora --llm offline`

## Etapas y archivos intermedios

| Etapa | Módulo | Entrada | Salida |
|---|---|---|---|
| ingest | `ingest/router.py` | `raw/` | `interim/fragmentos.jsonl`, `inventario.json`, `logs_eventos/*.csv` |
| anonymize | `privacy/anonymize.py` | fragmentos | `fragmentos_anon.jsonl`, `mapa_anonimizacion.json` (solo local) |
| analyze | `analyze/process_extract.py`, `process_mining.py`, `consolidate.py` | fragmentos anon + logs + cuestionario | `processed/diagnostico.json` |
| score | `score/opportunity.py` | diagnóstico | oportunidades priorizadas en `diagnostico.json` |
| report | `report/` | diagnóstico | `outputs/*.docx`, `*.md` |

Cada etapa lee y escribe en disco, así que se puede usar sola en otro proyecto, por ejemplo `from aiaudit.ingest.tabular import detectar_log_eventos`.

## Cómo funciona cada parte

- **Router**: clasifica cada archivo por extensión (o por MIME si no la reconoce), descomprime los zip con protección contra rutas fuera del destino y envía cada archivo a su extractor. Si un archivo falla queda registrado en `inventario.json` y el pipeline sigue.
- **Documentos**: Docling (mantiene la estructura, las tablas y el OCR de PDFs escaneados), con MarkItDown como respaldo.
- **Tablas**: resumen por hoja (columnas, tipos, nulos, muestra). Si una tabla tiene columnas de caso + actividad + fecha, se guarda como **log de eventos**.
- **Process mining** (pm4py): sobre esos logs mide las variantes del flujo, la duración de los casos, los cuellos de botella (la espera media entre actividades) y el % de retrabajo. Es la evidencia más objetiva del diagnóstico.
- **Comunicaciones**: correos `.eml/.mbox/.msg` y chats de WhatsApp agrupados por día.
- **Audio/imagen**: faster-whisper (transcripción con marcas de tiempo) y RapidOCR.
- **Anonimización**: en dos pasadas. Primero aprende los nombres completos del corpus (NER de Presidio/spaCy + participantes de los chats); después reemplaza el nombre completo y sus partes con el mismo token (`<PERSONA_3>`). Además usa regex para correos, teléfonos, RUT, DNI, CUIT, tarjetas e IBAN. Los teléfonos y DNI exigen un prefijo `+` o una etiqueta, para no borrar montos de presupuestos.
- **Extracción con LLM**: lotes de fragmentos más el catálogo del sector (`config/taxonomia_procesos.yaml`). Usa OpenAI Structured Outputs, así que la respuesta siempre cumple el esquema pydantic `ExtraccionLLM`. Cada llamada queda en `interim/llm_log.jsonl`, que sirve para auditar que no salió PII y para cotizar el costo.
- **Consolidación**: une los procesos por id del catálogo o por nombre similar, sube la confianza cuando hay varias fuentes que coinciden y cruza con el cuestionario.
- **Priorización**: `ahorro = horas_mes × automatizable × costo_hora` y `puntaje = ahorro × dolor/3 × confianza / complejidad`. La complejidad define la fase (1-2: quick wins, 3: integraciones, 4-5: desarrollo a medida).

## Advertencias

- **Sin el cuestionario el ROI es un supuesto.** Los procesos sin horas declaradas usan `horas_mes_supuesto` y quedan marcados como "No validado" en los informes.
- El modo `offline` es solo una línea base: solo coincide palabras clave y produce falsos positivos. El diagnóstico real se hace con el LLM.
- pm4py es AGPL-3.0: se puede usar internamente, pero hay que revisar la licencia antes de distribuirlo dentro de un producto para el cliente.
- Docling y Whisper descargan modelos desde HuggingFace la primera vez que se usan.
- Branding: si existe `src/aiaudit/report/templates/base.docx` (con tu logo y estilos), se usa como base de todos los informes.
- El modelo de OpenAI y sus precios se configuran en `config/settings.yaml`; verifícalos en tu cuenta.
