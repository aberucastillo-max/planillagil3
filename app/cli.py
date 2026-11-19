"""Comandos de administración.
   python -m app.cli init-db
   python -m app.cli crear-cuenta --nombre "Mi estudio" --plan GRATIS"""
import argparse

from .db import SessionLocal, engine
from .models import Base, Cuenta
from .security import generar_api_key, hash_key
from .seed import sembrar_cc, sembrar_mype, sembrar_rg728

PLANES = {"GRATIS": (1, 20), "FREELANCER": (20, 10)}   # (max_empresas, max_trabajadores por empresa) - ajustables


def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        sembrar_rg728(db)
        sembrar_mype(db)
        sembrar_cc(db)
        from .suscripcion import sembrar_planes
        sembrar_planes(db)
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


def crear_usuario(cuenta_id: int, email: str, nombre: str, rol: str = "PROPIETARIO"):
    from . import usuarios as U
    from .models import Usuario
    db = SessionLocal()
    try:
        temp = U.clave_temporal()
        db.add(Usuario(cuenta_id=cuenta_id, email=email.strip().lower(), nombre=nombre, rol=rol, clave_hash=U.hash_clave(temp), debe_cambiar_clave=True))
        db.commit()
    finally:
        db.close()
    print(f"Usuario creado. Contraseña temporal (se pedirá cambiarla al ingresar; no se vuelve a mostrar):\n{temp}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    c = sub.add_parser("crear-cuenta")
    c.add_argument("--nombre", required=True)
    c.add_argument("--plan", choices=list(PLANES), default="GRATIS")
    c.add_argument("--max-empresas", type=int)
    c.add_argument("--max-trabajadores", type=int)
    u = sub.add_parser("crear-usuario")
    u.add_argument("--cuenta-id", type=int, required=True)
    u.add_argument("--email", required=True)
    u.add_argument("--nombre", required=True)
    u.add_argument("--rol", choices=["PROPIETARIO", "OPERADOR", "CONSULTA"], default="PROPIETARIO")
    a = ap.parse_args()
    if a.cmd == "init-db":
        init_db()
    elif a.cmd == "crear-usuario":
        crear_usuario(a.cuenta_id, a.email, a.nombre, a.rol)
    else:
        crear_cuenta(a.nombre, a.plan, a.max_empresas, a.max_trabajadores)
