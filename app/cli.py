"""Comandos de administración.
   python -m app.cli init-db
   python -m app.cli crear-cuenta --nombre "Mi estudio" --plan GRATIS"""
import argparse

from .db import SessionLocal, engine
from .models import Base, Cuenta
from .security import generar_api_key, hash_key
from .seed import sembrar_rg728

PLANES = {"GRATIS": (1, 10), "FREELANCER": (20, 10)}   # (max_empresas, max_trabajadores por empresa) - ajustables


def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        sembrar_rg728(db)
    finally:
        db.close()
    print("Tablas creadas y Diccionario RG_728 v1 sembrado.")


def crear_cuenta(nombre: str, plan: str, max_empresas=None, max_trabajadores=None):
    me, mt = PLANES[plan]
    db = SessionLocal()
    try:
        clave = generar_api_key()
        db.add(Cuenta(nombre=nombre, plan=plan, max_empresas=max_empresas or me,
                      max_trabajadores=max_trabajadores or mt, api_key_hash=hash_key(clave)))
        db.commit()
    finally:
        db.close()
    print(f"Cuenta creada. API key (guárdela ahora; no se vuelve a mostrar):\n{clave}")
    return clave


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    c = sub.add_parser("crear-cuenta")
    c.add_argument("--nombre", required=True)
    c.add_argument("--plan", choices=list(PLANES), default="GRATIS")
    c.add_argument("--max-empresas", type=int)
    c.add_argument("--max-trabajadores", type=int)
    a = ap.parse_args()
    if a.cmd == "init-db":
        init_db()
    else:
        crear_cuenta(a.nombre, a.plan, a.max_empresas, a.max_trabajadores)
