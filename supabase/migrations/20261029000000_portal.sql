-- Portal de clientes aiaudit: proyectos, archivos, cuestionario, cola de trabajos y resultados.
-- Roles: 'admin' (consultora) ve todo; 'cliente' solo sube archivos y llena el cuestionario de SUS proyectos.
-- Los clientes nunca ven trabajos ni resultados: los informes los entrega la consultora.
-- Primer admin: tras registrarte, ejecutar  update public.perfiles set rol = 'admin' where email = 'tu@correo';

create extension if not exists pgcrypto;

-- ---------- Tablas ----------

create table public.perfiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  nombre text,
  rol text not null default 'cliente' check (rol in ('admin', 'cliente')),
  created_at timestamptz not null default now()
);

create table public.proyectos (
  id uuid primary key default gen_random_uuid(),
  empresa text not null,
  sector text not null default 'pyme' check (sector in ('construccion', 'pyme')),
  estado text not null default 'recibiendo'
    check (estado in ('recibiendo', 'enviado', 'procesando', 'analizado', 'entregado')),
  retencion_dias int not null default 90,       -- dias que se guardan los archivos crudos tras el analisis
  creado_por uuid references auth.users (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.miembros (
  proyecto_id uuid not null references public.proyectos (id) on delete cascade,
  usuario_id uuid not null references auth.users (id) on delete cascade,
  primary key (proyecto_id, usuario_id)
);

create table public.archivos (
  id uuid primary key default gen_random_uuid(),
  proyecto_id uuid not null references public.proyectos (id) on delete cascade,
  ruta text not null unique,                    -- ruta en el bucket: <proyecto_id>/<uuid>-<nombre>
  nombre text not null,
  tamano bigint,
  mime text,
  subido_por uuid references auth.users (id) default auth.uid(),
  created_at timestamptz not null default now()
);

create table public.cuestionarios (
  proyecto_id uuid primary key references public.proyectos (id) on delete cascade,
  datos jsonb not null default '{}'::jsonb,     -- misma estructura que config/cuestionario.yaml
  updated_at timestamptz not null default now()
);

create table public.trabajos (
  id uuid primary key default gen_random_uuid(),
  proyecto_id uuid not null references public.proyectos (id) on delete cascade,
  estado text not null default 'pendiente' check (estado in ('pendiente', 'ejecutando', 'ok', 'error')),
  proveedor_llm text not null default 'openai' check (proveedor_llm in ('openai', 'ollama', 'offline')),
  etapa text,
  mensaje text,
  creado_por uuid references auth.users (id),
  created_at timestamptz not null default now(),
  iniciado_at timestamptz,
  terminado_at timestamptz
);

create table public.resultados (
  proyecto_id uuid primary key references public.proyectos (id) on delete cascade,
  diagnostico jsonb not null,
  informes jsonb not null default '[]'::jsonb,  -- rutas en el bucket 'informes'
  resumen jsonb not null default '{}'::jsonb,   -- salida de cada etapa (costo LLM, conteos)
  updated_at timestamptz not null default now()
);

create index on public.archivos (proyecto_id);
create index on public.trabajos (estado, created_at);
create index on public.miembros (usuario_id);

-- ---------- Funciones auxiliares (security definer para no recursar en RLS) ----------

create or replace function public.es_admin() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.perfiles where id = auth.uid() and rol = 'admin');
$$;

create or replace function public.es_miembro(p uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.miembros where proyecto_id = p and usuario_id = auth.uid());
$$;

-- Version texto para Storage (la primera carpeta de la ruta es el id del proyecto)
create or replace function public.es_miembro_txt(p text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.miembros where proyecto_id::text = p and usuario_id = auth.uid());
$$;

create or replace function public.proyecto_abierto(p uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.proyectos where id = p and estado = 'recibiendo');
$$;

create or replace function public.proyecto_abierto_txt(p text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.proyectos where id::text = p and estado = 'recibiendo');
$$;

-- Perfil automatico al crear un usuario (invitacion)
create or replace function public.crear_perfil() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.perfiles (id, email) values (new.id, new.email) on conflict (id) do nothing;
  return new;
end;
$$;

create trigger al_crear_usuario after insert on auth.users
  for each row execute function public.crear_perfil();

create or replace function public.tocar_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger proyectos_updated before update on public.proyectos for each row execute function public.tocar_updated_at();
create trigger cuestionarios_updated before update on public.cuestionarios for each row execute function public.tocar_updated_at();

-- El cliente cierra la entrega: despues ya no puede subir ni editar
create or replace function public.enviar_proyecto(p uuid) returns void
language plpgsql security definer set search_path = '' as $$
begin
  if not public.es_miembro(p) then
    raise exception 'sin acceso al proyecto';
  end if;
  update public.proyectos set estado = 'enviado' where id = p and estado = 'recibiendo';
end;
$$;

-- Cola: el worker toma un trabajo pendiente sin que dos workers tomen el mismo
create or replace function public.tomar_trabajo() returns setof public.trabajos
language sql security definer set search_path = '' as $$
  update public.trabajos t set estado = 'ejecutando', iniciado_at = now(), etapa = 'inicio'
  where t.id = (select id from public.trabajos where estado = 'pendiente'
                order by created_at for update skip locked limit 1)
  returning t.*;
$$;

revoke execute on function public.tomar_trabajo() from public, anon, authenticated;
revoke execute on function public.crear_perfil() from public, anon, authenticated;

-- ---------- RLS ----------

alter table public.perfiles enable row level security;
alter table public.proyectos enable row level security;
alter table public.miembros enable row level security;
alter table public.archivos enable row level security;
alter table public.cuestionarios enable row level security;
alter table public.trabajos enable row level security;
alter table public.resultados enable row level security;

create policy perfiles_ver on public.perfiles for select to authenticated
  using (id = (select auth.uid()) or (select public.es_admin()));
create policy perfiles_admin on public.perfiles for update to authenticated
  using ((select public.es_admin())) with check ((select public.es_admin()));

create policy proyectos_ver on public.proyectos for select to authenticated
  using ((select public.es_admin()) or public.es_miembro(id));
create policy proyectos_admin on public.proyectos for all to authenticated
  using ((select public.es_admin())) with check ((select public.es_admin()));

create policy miembros_ver on public.miembros for select to authenticated
  using (usuario_id = (select auth.uid()) or (select public.es_admin()));
create policy miembros_admin on public.miembros for all to authenticated
  using ((select public.es_admin())) with check ((select public.es_admin()));

create policy archivos_ver on public.archivos for select to authenticated
  using ((select public.es_admin()) or public.es_miembro(proyecto_id));
create policy archivos_subir on public.archivos for insert to authenticated
  with check (public.es_miembro(proyecto_id) and public.proyecto_abierto(proyecto_id)
              and subido_por = (select auth.uid()) and split_part(ruta, '/', 1) = proyecto_id::text);
create policy archivos_borrar on public.archivos for delete to authenticated
  using ((select public.es_admin()) or (subido_por = (select auth.uid()) and public.proyecto_abierto(proyecto_id)));

create policy cuestionario_ver on public.cuestionarios for select to authenticated
  using ((select public.es_admin()) or public.es_miembro(proyecto_id));
create policy cuestionario_crear on public.cuestionarios for insert to authenticated
  with check ((select public.es_admin()) or (public.es_miembro(proyecto_id) and public.proyecto_abierto(proyecto_id)));
create policy cuestionario_editar on public.cuestionarios for update to authenticated
  using ((select public.es_admin()) or (public.es_miembro(proyecto_id) and public.proyecto_abierto(proyecto_id)))
  with check ((select public.es_admin()) or (public.es_miembro(proyecto_id) and public.proyecto_abierto(proyecto_id)));

-- trabajos y resultados: solo admin (el worker usa la service role, que no pasa por RLS)
create policy trabajos_admin on public.trabajos for all to authenticated
  using ((select public.es_admin())) with check ((select public.es_admin()));
create policy resultados_admin on public.resultados for all to authenticated
  using ((select public.es_admin())) with check ((select public.es_admin()));

-- ---------- Storage ----------
-- 50 MB es el maximo por archivo del plan gratuito; en planes pagos se puede subir.

insert into storage.buckets (id, name, public, file_size_limit)
values ('archivos-clientes', 'archivos-clientes', false, 52428800),
       ('informes', 'informes', false, 52428800)
on conflict (id) do nothing;

create policy clientes_subir on storage.objects for insert to authenticated
  with check (bucket_id = 'archivos-clientes'
              and public.es_miembro_txt((storage.foldername(name))[1])
              and public.proyecto_abierto_txt((storage.foldername(name))[1]));
create policy clientes_ver on storage.objects for select to authenticated
  using (bucket_id = 'archivos-clientes'
         and ((select public.es_admin()) or public.es_miembro_txt((storage.foldername(name))[1])));
create policy clientes_borrar on storage.objects for delete to authenticated
  using (bucket_id = 'archivos-clientes' and owner_id = (select auth.uid())::text
         and public.proyecto_abierto_txt((storage.foldername(name))[1]));
create policy informes_admin on storage.objects for all to authenticated
  using (bucket_id = 'informes' and (select public.es_admin()))
  with check (bucket_id = 'informes' and (select public.es_admin()));
