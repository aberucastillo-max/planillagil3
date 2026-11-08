import os
os.environ["DATABASE_URL"] = "sqlite://"   # base en memoria solo para pruebas

import pytest
from fastapi.testclient import TestClient

from app.cli import crear_cuenta  # noqa: F401
from app.db import SessionLocal, engine
from app.main import app
from app.models import Base, Cuenta
from app.security import generar_api_key, hash_key
from app.seed import sembrar_rg728


def nueva_cuenta(nombre, max_empresas=2, max_trabajadores=10):
    clave = generar_api_key()
    db = SessionLocal()
    db.add(Cuenta(nombre=nombre, plan="TEST", max_empresas=max_empresas, max_trabajadores=max_trabajadores,
                  api_key_hash=hash_key(clave)))
    db.commit()
    db.close()
    return clave


@pytest.fixture()
def entorno():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    sembrar_rg728(db)
    db.close()
    return TestClient(app), nueva_cuenta
