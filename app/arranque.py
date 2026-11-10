"""Preparación AUTOMÁTICA de la base al iniciar el servidor (idempotente: puede correr en cada arranque)."""
import logging
import os

from sqlalchemy import inspect, text

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
    if engine.dialect.name == "postgresql":      # bases creadas antes de la etapa de liquidaciones
        with engine.begin() as c:
            c.execute(text("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS tipo_contrato VARCHAR(12) NOT NULL DEFAULT 'INDEFINIDO'"))
            c.execute(text("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS fecha_fin_contrato DATE"))
            c.execute(text("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS clase_despido VARCHAR(12)"))
            for col, tipo in (("area", "VARCHAR(100)"), ("centro_costo", "VARCHAR(100)"), ("categoria", "VARCHAR(30) DEFAULT 'EMPLEADOS'"),
                              ("calificacion", "VARCHAR(60)")):
                c.execute(text(f"ALTER TABLE empleados ADD COLUMN IF NOT EXISTS {col} {tipo}"))
            c.execute(text("ALTER TABLE liquidacion_detalle ADD COLUMN IF NOT EXISTS descripcion VARCHAR(150)"))
            c.execute(text("ALTER TABLE liquidacion_detalle ADD COLUMN IF NOT EXISTS manual BOOLEAN NOT NULL DEFAULT FALSE"))
            c.execute(text("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS tipo_comision VARCHAR(6) NOT NULL DEFAULT 'FLUJO'"))
            c.execute(text("ALTER TABLE diccionario_afp ADD COLUMN IF NOT EXISTS comision_mixta_flujo NUMERIC(7,4) NOT NULL DEFAULT 0"))
            c.execute(text("ALTER TABLE diccionario_afp ADD COLUMN IF NOT EXISTS comision_saldo NUMERIC(7,4) NOT NULL DEFAULT 0"))
            c.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS usa_quincena BOOLEAN NOT NULL DEFAULT FALSE"))
            c.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS quincena_modo VARCHAR(12) NOT NULL DEFAULT 'PORCENTAJE'"))
            c.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS quincena_valor NUMERIC(12,2) NOT NULL DEFAULT 50"))
            for col, tipo in (("direccion_fiscal", "VARCHAR(255)"), ("representante_legal", "VARCHAR(150)"), ("cargo_representante", "VARCHAR(100)")):
                c.execute(text(f"ALTER TABLE empresas ADD COLUMN IF NOT EXISTS {col} {tipo}"))
    db = SessionLocal()
    try:
        sembrar_rg728(db)
        # Desde la etapa 4 la liquidación NO se guarda como borrador: solo existe cerrada. Se limpian los borradores antiguos.
        if "liquidaciones" in set(inspect(engine).get_table_names()):
            ids = [r[0] for r in db.execute(text("SELECT id FROM liquidaciones WHERE estado = 'ABIERTA'"))]
            if ids:
                lista = ",".join(str(i) for i in ids)
                db.execute(text(f"DELETE FROM liquidacion_detalle WHERE liquidacion_id IN ({lista})"))
                db.execute(text(f"DELETE FROM liquidaciones WHERE id IN ({lista})"))
                db.commit()
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
