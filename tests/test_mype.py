"""MYPE: regímenes por empresa, norma, salud, asignación familiar, gratificación/CTS y liquidación."""
from decimal import Decimal

from app.db import SessionLocal
from app.models import Diccionario, Empresa
from app.seed import sembrar_mype

H = lambda k: {"X-API-Key": k}
API = "/api/v1"


def _env(entorno):
    cli, nueva = entorno
    db = SessionLocal(); sembrar_mype(db); db.close()
    return cli, nueva("Cuenta MYPE")


def _empresa(cli, k, **kw):
    r = cli.post(f"{API}/empresas", headers=H(k), json={"ruc": "20111111111", "razon_social": "Mype SAC", "representante_legal": "Juan Perez", "representante_dni": "12345678", **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _trab(cli, k, eid, af=False, ingreso="2025-01-02"):
    r = cli.post(f"{API}/empresas/{eid}/empleados", headers=H(k), json={"nombres": "Ana", "apellidos": "Ruiz", "documento_identidad": "45678912",
                 "fecha_ingreso": ingreso, "sueldo_basico": 2000, "sistema_pensionario": "ONP", "asignacion_familiar": af})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _planilla(cli, k, eid, mes=10):
    pid = cli.post(f"{API}/empresas/{eid}/periodos", headers=H(k), json={"anio": 2026, "mes": mes}).json()["id"]
    assert cli.post(f"{API}/empresas/{eid}/planillas/{pid}/recalcular", headers=H(k)).status_code == 200
    return pid, {c["codigo"]: float(c["monto"]) for c in cli.get(f"{API}/empresas/{eid}/planillas/{pid}/resultados/{_trab.last}/detalle", headers=H(k)).json()["conceptos"]}


def test_empresa_guarda_datos_y_normas(entorno):
    cli, k = _env(entorno)
    eid = _empresa(cli, k, regimen="MYPE_MICRO", norma_mype="LEY_32353", salud_micro="SIS")
    e = [x for x in cli.get(f"{API}/empresas", headers=H(k)).json() if x["id"] == eid][0]
    assert e["regimen"] == "MYPE_MICRO" and e["norma_mype"] == "LEY_32353" and e["salud_micro"] == "SIS" and e["representante_dni"] == "12345678"
    db = SessionLocal(); d = db.get(Diccionario, db.get(Empresa, eid).diccionario_id); assert (d.codigo, d.version) == ("MYPE_MICRO", 2); db.close()
    assert cli.patch(f"{API}/empresas/{eid}", headers=H(k), json={"norma_mype": "DS_013_2013"}).status_code == 200      # sin periodos: se puede cambiar
    assert cli.post(f"{API}/empresas", headers=H(k), json={"ruc": "20222222222", "razon_social": "X", "representante_dni": "123"}).status_code == 422
    assert "SIS" in cli.get(f"{API}/mype/advertencias", headers=H(k)).json()["salud_micro"]


def test_micro_sis_sin_gratificacion_ni_cts(entorno):
    cli, k = _env(entorno)
    eid = _empresa(cli, k, regimen="MYPE_MICRO", salud_micro="SIS")
    _trab.last = _trab(cli, k, eid)
    _, det = _planilla(cli, k, eid, mes=7)
    assert det["@SIS"] == 15.0 and "@ESSALUD" not in det and "@GRATIFICACION" not in det
    r = cli.post(f"{API}/empresas/{eid}/cts-semestral/calcular", headers=H(k), json={"anio": 2026, "semestre": "NOV"})
    assert r.status_code == 409 and "CTS" in r.json()["detail"]


def test_micro_essalud_y_pequena_gratificacion_mitad(entorno):
    cli, k = _env(entorno)
    e1 = _empresa(cli, k, regimen="MYPE_MICRO", salud_micro="ESSALUD")
    _trab.last = _trab(cli, k, e1)
    _, det = _planilla(cli, k, e1)
    assert det["@ESSALUD"] == 180.0 and "@SIS" not in det
    e2 = cli.post(f"{API}/empresas", headers=H(k), json={"ruc": "20222222222", "razon_social": "Peq SAC", "regimen": "MYPE_PEQUENA"}).json()["id"]
    _trab.last = _trab(cli, k, e2)
    _, det = _planilla(cli, k, e2, mes=7)
    assert det["@GRATIFICACION"] == 1000.0 and det["@BONIF_EXTRA_29351"] == 90.0 and det["@ESSALUD"] == 180.0     # media remuneración por semestre; la gratificación no paga EsSalud


def test_asignacion_familiar_configurable(entorno):
    cli, k = _env(entorno)
    eid = _empresa(cli, k, regimen="MYPE_PEQUENA", asig_fam_mype=False)
    _trab.last = _trab(cli, k, eid, af=True)
    pid, det = _planilla(cli, k, eid)
    assert "@ASIGNACION_FAMILIAR" not in det
    cli.patch(f"{API}/empresas/{eid}", headers=H(k), json={"asig_fam_mype": True})
    assert cli.post(f"{API}/empresas/{eid}/planillas/{pid}/recalcular", headers=H(k)).status_code == 200
    d = {c["codigo"]: float(c["monto"]) for c in cli.get(f"{API}/empresas/{eid}/planillas/{pid}/resultados/{_trab.last}/detalle", headers=H(k)).json()["conceptos"]}
    assert d["@ASIGNACION_FAMILIAR"] == 113.0
