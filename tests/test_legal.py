"""Aceptación legal registrada, páginas públicas y exportar/suprimir una empresa."""
from app.db import SessionLocal
from app.models import Aceptacion, Auditoria, Empleado, Empresa
from tests.test_auth import API, _cuenta_con_propietario, _sesion

H = lambda k: {"X-API-Key": k}


def test_paginas_y_version_publicas(entorno):
    cli, _ = entorno
    v = cli.get(f"{API}/legal/version").json()
    assert v["version"] and len(v["documentos"]) == 3
    for c in ("terminos", "privacidad", "encargo"):
        r = cli.get(f"/legal/{c}")
        assert r.status_code == 200 and "Versión" in r.text
    assert cli.get("/legal/otro").status_code == 404


def test_aceptacion_registrada(entorno, monkeypatch):
    cli, _ = entorno
    cid, temp = _cuenta_con_propietario(cli, monkeypatch)
    h = _sesion(cli, "dueno@x.pe", temp)
    assert cli.get(f"{API}/auth/yo", headers=h).json()["legal_pendiente"] is True
    assert cli.post(f"{API}/auth/aceptar-legal", headers=h).status_code == 200
    assert cli.post(f"{API}/auth/aceptar-legal", headers=h).status_code == 200      # idempotente
    assert cli.get(f"{API}/auth/yo", headers=h).json()["legal_pendiente"] is False
    db = SessionLocal()
    assert db.query(Aceptacion).count() == 1
    db.close()
    lista = cli.get(f"{API}/legal/aceptaciones", headers=h).json()
    assert lista[0]["email"] == "dueno@x.pe" and lista[0]["version"]


def test_exportar_y_suprimir_empresa(entorno, monkeypatch):
    cli, _ = entorno
    cid, temp = _cuenta_con_propietario(cli, monkeypatch)
    h = _sesion(cli, "dueno@x.pe", temp)
    eid = cli.post(f"{API}/empresas", headers=h, json={"ruc": "20111111111", "razon_social": "Borrable SAC"}).json()["id"]
    r = cli.post(f"{API}/empresas/{eid}/empleados", headers=h, json={
        "nombres": "Ana", "apellidos": "Perez Lopez", "documento_identidad": "12345678",
        "fecha_ingreso": "2026-01-01", "sueldo_basico": 3000})
    assert r.status_code in (200, 201), r.text
    ex = cli.get(f"{API}/empresas/{eid}/exportar-datos", headers=h)
    assert ex.status_code == 200 and "attachment" in ex.headers["content-disposition"]
    js = ex.json()
    assert js["empresa"]["ruc"] == "20111111111" and len(js["empleados"]) == 1 and js["sueldos"]
    assert cli.delete(f"{API}/empresas/{eid}", headers=h).status_code == 422                       # falta confirmar RUC
    assert cli.delete(f"{API}/empresas/{eid}?confirmar_ruc=999", headers=h).status_code == 422
    ok = cli.delete(f"{API}/empresas/{eid}?confirmar_ruc=20111111111", headers=h)
    assert ok.status_code == 200 and ok.json()["suprimidos"]["empleados"] == 1
    db = SessionLocal()
    assert db.query(Empresa).count() == 0 and db.query(Empleado).count() == 0
    assert db.query(Auditoria).filter_by(accion="SUPRIMIR_EMPRESA").count() == 1
    db.close()
    assert cli.get(f"{API}/empresas/{eid}/exportar-datos", headers=h).status_code == 404


def test_operador_no_puede_suprimir(entorno, monkeypatch):
    cli, _ = entorno
    cid, temp = _cuenta_con_propietario(cli, monkeypatch)
    h = _sesion(cli, "dueno@x.pe", temp)
    eid = cli.post(f"{API}/empresas", headers=h, json={"ruc": "20222222222", "razon_social": "X SAC"}).json()["id"]
    r = cli.post(f"{API}/usuarios", headers=h, json={"email": "op@x.pe", "nombre": "Operador", "rol": "OPERADOR"})
    ho = _sesion(cli, "op@x.pe", r.json()["clave_temporal"])
    assert cli.delete(f"{API}/empresas/{eid}?confirmar_ruc=20222222222", headers=ho).status_code == 403
    assert cli.get(f"{API}/empresas/{eid}/exportar-datos", headers=ho).status_code == 403
