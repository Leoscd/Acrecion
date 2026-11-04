# Despliegue del portal

Arquitectura:

```
Cliente (navegador) ──> Caddy (HTTPS) ──> web: FastAPI (páginas + API admin)
        │                                        │
        └── supabase-js ──> Supabase: Auth (enlace mágico) · Postgres con RLS · Storage privado
                                                 ▲
                         worker (VPS) ──────────┘  toma trabajos de la cola, corre aiaudit, sube informes
```

- El cliente solo sube archivos y llena el cuestionario de sus proyectos. RLS impide que vea otros proyectos, trabajos o resultados.
- La clave `SUPABASE_SERVICE_ROLE_KEY` solo vive en el VPS (web y worker). Al navegador solo llega la clave publicable.
- Ningún dato del cliente queda en el VPS: al terminar cada trabajo el worker borra la carpeta completa del proyecto (archivos, texto extraído y mapa de anonimización). Lo que se conserva vive en Supabase.
- En Storage, los archivos crudos se borran automáticamente cuando vence `retencion_dias` (90 por defecto). Desde el panel puedes eliminar un proyecto completo en cualquier momento.

## 1. Supabase (una vez)

1. Crea o reactiva un proyecto. Conviene la región `sa-east-1` (São Paulo) si tus clientes están en Latinoamérica.
2. Aplica la migración `supabase/migrations/20261029000000_portal.sql` (SQL Editor, o `supabase db push` con la CLI).
3. **Authentication > Sign In / Providers**: deja Email activo y **desactiva "Allow new users to sign up"**, para que solo entren usuarios invitados.
4. **Authentication > URL Configuration**:
   - Site URL: `https://portal.tudominio.com`
   - Redirect URLs: `https://portal.tudominio.com/portal`
5. **Authentication > SMTP**: configura un SMTP propio (Resend, Brevo, etc.). El correo incluido en Supabase tiene un límite muy bajo de envíos por hora y no sirve para invitar clientes.
6. Crea tu usuario admin después de levantar el VPS (paso 2):
   ```bash
   docker compose run --rm web aiaudit-portal crear-admin tu@correo.com
   ```
   Te llega la invitación por correo y entras a `/admin`. También existen `aiaudit-portal admins` y `aiaudit-portal quitar-admin`.

## 2. VPS

Requisitos: Docker con Compose, los puertos 80 y 443 abiertos, y un registro DNS `A` de `portal.tudominio.com` apuntando a la IP del VPS. Mínimo recomendado: 4 GB de RAM (Docling y Whisper usan bastante memoria) y 20 GB de disco.

```bash
git clone https://github.com/Leoscd/Acrecion.git && cd Acrecion
cp .env.example .env    # completar todas las variables
docker compose up -d --build
docker compose logs -f worker
```

Caddy obtiene el certificado HTTPS automáticamente.

- **Modelos:** la imagen descarga los modelos de Docling, Whisper y OCR durante el build, así el primer análisis no espera. Para una imagen más liviana, usa `docker compose build --build-arg PRECARGAR_MODELOS=0`; en ese caso se descargan en el primer uso y quedan en el volumen `modelos`.
- **Salud:** `docker compose ps` muestra `healthy`/`unhealthy`. El `web` responde en `/api/salud`. El `worker` marca un latido en cada vuelta y en cada etapa; queda `unhealthy` si pasa más de 3 horas sin señal (un archivo de audio muy largo puede tardar).

Para actualizar:
```bash
git pull && docker compose up -d --build
```

## 3. Flujo de uso

1. En `/admin` creas el proyecto con el correo del cliente, y Supabase le envía la invitación.
2. El cliente entra con el enlace, sube sus archivos, llena el cuestionario y presiona **Enviar para análisis** (desde ahí su carga queda bloqueada).
3. Tú presionas **Procesar** y eliges el LLM. El panel muestra la etapa del worker en vivo.
4. Revisas los resultados (KPIs, gráfico, oportunidades, procesos) y descargas los DOCX.
5. Validas con el cliente las horas marcadas como supuestas. Si hace falta, usas **Reabrir carga** para que corrija el cuestionario, y vuelves a procesar.
6. Entregas los informes y marcas el proyecto como **Entregado**.

## Límites del plan gratuito de Supabase

- 50 MB por archivo y 1 GB de Storage en total: alcanza para unos pocos clientes a la vez. Al pasar a un plan pago, sube `MAX_ARCHIVO_MB` y el `file_size_limit` de los buckets.
- Los proyectos gratuitos se pausan tras una semana sin actividad. Para clientes reales conviene un plan pago.
