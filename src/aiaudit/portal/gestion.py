"""Tareas de administracion del portal desde la terminal (usa la service role).

aiaudit-portal crear-admin tu@correo.com     invita (si no existe) y le da rol admin
aiaudit-portal quitar-admin otro@correo.com  lo deja como cliente
aiaudit-portal admins                        lista los administradores
En Docker: docker compose run --rm web aiaudit-portal crear-admin tu@correo.com
"""
from __future__ import annotations

import typer

from aiaudit.portal.supa import env, servicio

app = typer.Typer(add_completion=False, help="Administracion del portal aiaudit.")


def _perfil(email: str) -> dict | None:
    datos = servicio().table("perfiles").select("id,email,rol").eq("email", email.lower()).execute().data
    return datos[0] if datos else None


def crear_admin(email: str) -> tuple[str, bool]:
    """Devuelve (usuario_id, invitado). Si el usuario no existe, Supabase le envia el correo de invitacion."""
    sb = servicio()
    perfil = _perfil(email)
    invitado = perfil is None
    if invitado:
        # /portal esta en las Redirect URLs de Supabase y redirige a los admins a /admin
        uid = sb.auth.admin.invite_user_by_email(email.lower(), {"redirect_to": f"{env('PUBLIC_URL')}/portal"}).user.id
        # el trigger crea el perfil al crear el usuario; upsert por si el trigger aun no existe
        sb.table("perfiles").upsert({"id": uid, "email": email.lower(), "rol": "admin"}).execute()
    else:
        uid = perfil["id"]
    sb.table("perfiles").update({"rol": "admin"}).eq("id", uid).execute()
    return uid, invitado


@app.command("crear-admin")
def crear_admin_cmd(email: str):
    """Da rol admin a un correo (lo invita si no tiene cuenta)."""
    _, invitado = crear_admin(email)
    typer.echo(f"{email} ahora es admin." + (" Se envio la invitacion por correo." if invitado else ""))


@app.command("quitar-admin")
def quitar_admin(email: str):
    """Quita el rol admin (queda como cliente)."""
    perfil = _perfil(email)
    if not perfil:
        typer.echo(f"No existe un usuario con el correo {email}", err=True)
        raise typer.Exit(1)
    servicio().table("perfiles").update({"rol": "cliente"}).eq("id", perfil["id"]).execute()
    typer.echo(f"{email} ya no es admin.")


@app.command()
def admins():
    """Lista los administradores."""
    for p in servicio().table("perfiles").select("email").eq("rol", "admin").execute().data:
        typer.echo(p["email"])


if __name__ == "__main__":
    app()
