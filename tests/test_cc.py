"""Construcción Civil: planilla semanal contra la tabla de la Federación (RM 197-2025-TR)."""
from app.db import SessionLocal
from app.seed import sembrar_cc, sembrar_mype

H = lambda k: {"X-API-Key": k}
API = "/api/v1"


def _cc(entorno, **kw):
    cli, nueva = entorno
    db = SessionLocal(); sembrar_cc(db); db.close()
    k = nueva("Cuenta CC")
    eid = cli.post(f"{API}/empresas", headers=H(k), json={"ruc": "20111111111", "razon_social": "Constructora SAC", "regimen": "CONSTRUCCION_CIVIL", **kw})
    assert eid.status_code == 201, eid.text
    return cli, k, eid.json()["id"]


def _obrero(cli, k, eid, tipo, doc, **kw):
    r = cli.post(f"{API}/empresas/{eid}/empleados", headers=H(k), json={"nombres": "Juan", "apellidos": tipo + doc, "documento_identidad": doc,
                 "fecha_ingreso": "2026-01-02", "tipo_obrero": tipo, "sistema_pensionario": "ONP", "centro_costo": "OBRA 001", **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _semana(cli, k, eid, lunes="2026-06-01"):
    r = cli.post(f"{API}/empresas/{eid}/periodos", headers=H(k), json={"anio": 2026, "mes": 6, "fase": "Semana", "fecha_inicio": lunes})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert cli.post(f"{API}/empresas/{eid}/planillas/{pid}/recalcular", headers=H(k)).status_code == 200
    return pid


def _det(cli, k, eid, pid, emp):
    return {c["codigo"]: float(c["monto"]) for c in cli.get(f"{API}/empresas/{eid}/planillas/{pid}/resultados/{emp}/detalle", headers=H(k)).json()["conceptos"]}


def test_operario_con_beneficios_semanales_igual_a_la_tabla(entorno):
    cli, k, eid = _cc(entorno)
    op = _obrero(cli, k, eid, "OPERARIO", "40000001")
    pid = _semana(cli, k, eid)                          # junio: gratificación Fiestas Patrias (divisor 210)
    d = _det(cli, k, eid, pid, op)
    assert (d["@JORNAL_BASICO"], d["@DSO"], d["@BUC"], d["@MOVILIDAD_CC"]) == (535.80, 89.30, 171.46, 51.60)
    assert (d["@INDEMNIZACION_CC"], d["@VACACIONES_TRUNCAS_CC"], d["@GRATIF_CC"], d["@BONIF_EXTRA_CC"]) == (80.37, 53.58, 119.07, 10.72)
    assert d["@SNP_ONP"] == 110.52 and d["@CONAFOVICER"] == 12.50
    f = cli.get(f"{API}/empresas/{eid}/planillas/{pid}/semanal", headers=H(k)).json()["filas"][0]
    assert abs(float(f["neto"]) - 988.87) <= 0.02                  # «Total Neto Semanal» de la tabla


def test_peon_oficial_y_especializados_sin_beneficios(entorno):
    cli, k, eid = _cc(entorno, cc_beneficios_semanales=False)
    ids = {t: _obrero(cli, k, eid, t, f"4000000{i}") for i, t in enumerate(["PEON", "OFICIAL", "OP_ELECTROMECANICO"], 2)}
    pid = _semana(cli, k, eid)
    neto = {f["tipo_obrero"]: float(f["neto"]) for f in cli.get(f"{API}/empresas/{eid}/planillas/{pid}/semanal", headers=H(k)).json()["filas"]}
    assert abs(neto["PEON"] - 523.60) <= 0.02 and abs(neto["OFICIAL"] - 575.84) <= 0.02          # «Pago Neto Semanal» sin beneficios
    d = _det(cli, k, eid, pid, ids["OP_ELECTROMECANICO"])
    assert d["@BAE"] == 117.88 and "@INDEMNIZACION_CC" not in d


def test_tarifas_editables_con_vigencia_y_navidad(entorno):
    cli, k, eid = _cc(entorno)
    op = _obrero(cli, k, eid, "OPERARIO", "40000009")
    t = cli.get(f"{API}/empresas/{eid}/cc/tarifas?fecha=2026-06-01", headers=H(k)).json()
    assert {x["clave"]: x["valor"] for x in t["tarifas"]}["JORNAL_OPERARIO"] in ("89.30", "89.300000")
    assert cli.put(f"{API}/empresas/{eid}/cc/tarifas", headers=H(k), json={"vigente_desde": "2026-06-15", "valores": {"JORNAL_OPERARIO": "100"}}).status_code == 200
    p1 = _semana(cli, k, eid, "2026-06-08")             # antes de la vigencia: convenio
    assert _det(cli, k, eid, p1, op)["@JORNAL_BASICO"] == 535.80
    p2 = _semana(cli, k, eid, "2026-06-15")             # desde la vigencia: valor de la empresa
    assert _det(cli, k, eid, p2, op)["@JORNAL_BASICO"] == 600.00
    r = cli.post(f"{API}/empresas/{eid}/periodos", headers=H(k), json={"anio": 2026, "mes": 12, "fase": "Semana", "fecha_inicio": "2026-12-07"}).json()["id"]
    cli.post(f"{API}/empresas/{eid}/cc/tarifas/restablecer", headers=H(k))
    cli.post(f"{API}/empresas/{eid}/planillas/{r}/recalcular", headers=H(k))
    assert _det(cli, k, eid, r, op)["@GRATIF_CC"] == 166.69          # Navidad: divisor 150


def test_validaciones_cc(entorno):
    cli, k, eid = _cc(entorno)
    assert cli.post(f"{API}/empresas/{eid}/empleados", headers=H(k), json={"nombres": "A", "apellidos": "B", "documento_identidad": "40000010", "fecha_ingreso": "2026-01-02"}).status_code == 422
    assert cli.post(f"{API}/empresas/{eid}/periodos", headers=H(k), json={"anio": 2026, "mes": 6, "fase": "Semana", "fecha_inicio": "2026-06-02"}).status_code == 422   # no es lunes


def test_carga_masiva_cc_toma_jornal_de_las_tarifas(entorno):
    from app import carga_masiva as CM
    from app.models import Cuenta, Empresa
    cli, k, eid = _cc(entorno)
    db = SessionLocal(); emp = db.get(Empresa, eid); cuenta = db.get(Cuenta, emp.cuenta_id)
    fila = {"_fila": 2, "apellidos": "PEREZ", "nombres": "LUIS", "documento": "40000077", "fecha_ingreso": "02/01/2026", "sistema_pensionario": "ONP", "tipo_obrero": "PEON"}
    r = CM.analizar(db, emp, cuenta, [fila, {**fila, "_fila": 3, "documento": "40000078", "tipo_obrero": ""}])
    db.close()
    txt = str(r)
    assert "Tipo de obrero obligatorio" in txt and "CREAR" in txt
