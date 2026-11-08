"""Preparación AUTOMÁTICA de la base al iniciar el servidor (idempotente: puede correr en cada arranque)."""
import logging
import os

from sqlalchemy import inspect

from .db import SessionLocal, engine
from .models import Base
from .seed import sembrar_rg728

log = logging.getLogger("planilla")
SQL_INTEGRIDAD = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sql", "integridad_postgres.sql")


def preparar_base():
    tablas = set(inspect(engine).get_table_names())
    if "empresas" in tablas and "cuentas" not in tablas:
        raise RuntimeError(
            "La base de Neon tiene tablas de la versión anterior. En Neon > SQL Editor ejecute una sola vez: "
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public;  y reinicie el servicio.")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        sembrar_rg728(db)
    finally:
        db.close()
    if engine.dialect.name == "postgresql" and os.path.exists(SQL_INTEGRIDAD):
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute(open(SQL_INTEGRIDAD, encoding="utf-8").read())
            raw.commit()
        except Exception as e:  # noqa
            raw.rollback()
            log.warning("No se pudo activar el bloqueo de periodos cerrados: %s", e)
        finally:
            raw.close()
