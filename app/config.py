"""Configuración leída del entorno. No hay secretos en el código."""
import os


def normalizar_url(url: str) -> str:
    """Neon entrega postgresql://...; SQLAlchemy necesita el driver explícito."""
    url = url.strip().strip('"').strip("'")
    for extra in ("&channel_binding=require", "channel_binding=require&", "?channel_binding=require"):
        url = url.replace(extra, "?" if extra.startswith("?") else "")
    if url.endswith("?"):
        url = url[:-1]
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Falta la variable de entorno DATABASE_URL (ver .env.example).")
    return normalizar_url(url)


def allowed_origins() -> list:
    raw = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5500,http://127.0.0.1:5500,https://cdpn.io,https://codepen.io",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]
