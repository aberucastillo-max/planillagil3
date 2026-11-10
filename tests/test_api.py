import io
import zipfile

H = lambda k: {"X-API-Key": k}


def _preparar(cli, clave):
    r = cli.post("/api/v1/empresas", json={"ruc": "20111111111", "razon_social": "Cliente Uno SAC"}, headers=H(clave))
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    ana = cli.post(f"/api/v1/empresas/{eid}/empleados", headers=H(clave), json={
        "nombres": "Ana", "apellidos": "Quispe Rojas", "documento_identidad": "45678912",
        "fecha_ingreso": "2025-03-01", "sueldo_basico": 4500, "sistema_pensionario": "AFP",
        "afp_nombre": "prima", "cuspp": "123456ABCDE1", "asignacion_familiar": True, "numero_cuenta": "194-1"}).json()["id"]
    luis = cli.post(f"/api/v1/empresas/{eid}/empleados", headers=H(clave), json={
        "nombres": "Luis", "apellidos": "Rojas Diaz", "documento_identidad": "40123456",
        "fecha_ingreso": "2026-10-21", "sueldo_basico": 3000, "sistema_pensionario": "ONP"}).json()["id"]
    pid = cli.post(f"/api/v1/empresas/{eid}/periodos", json={"anio": 2026, "mes": 10}, headers=H(clave)).json()["id"]
    return eid, ana, luis, pid


def test_flujo_completo(entorno):
    cli, nueva = entorno
    clave = nueva("Freelancer 1")
    eid, ana, luis, pid = _preparar(cli, clave)
    base = f"/api/v1/empresas/{eid}"

    # ausentismo y vacación de MESES ANTERIORES no deben afectar octubre (bug del main.py original)
    assert cli.post(f"{base}/empleados/{ana}/ausentismos", headers=H(clave),
                    json={"codigo_plame": "06", "fecha_inicio": "2026-09-01", "fecha_fin": "2026-09-07"}).status_code == 201
    assert cli.post(f"{base}/empleados/{ana}/vacaciones", headers=H(clave),
                    json={"fecha_inicio": "2026-01-05", "fecha_fin": "2026-01-11"}).status_code == 201
    # una falta en octubre sí cuenta
    cli.post(f"{base}/empleados/{ana}/ausentismos", headers=H(clave),
             json={"codigo_plame": "06", "fecha_inicio": "2026-10-05", "fecha_fin": "2026-10-05"})
    # cruce de fechas => 409
    assert cli.post(f"{base}/empleados/{ana}/ausentismos", headers=H(clave),
                    json={"codigo_plame": "06", "fecha_inicio": "2026-10-05", "fecha_fin": "2026-10-06"}).status_code == 409

    # Pre-Check de carga masiva: todo o nada
    mala = cli.put(f"{base}/periodos/{pid}/novedades", headers=H(clave), json=[
        {"documento": "45678912", "codigo": "@COMISIONES", "valor": "350.505"},
        {"documento": "99999999", "codigo": "@COMISIONES", "valor": "10"},
        {"documento": "45678912", "codigo": "@NOEXISTE", "valor": "10"},
        {"documento": "45678912", "codigo": "@HABER_BASICO", "valor": "10"}])
    assert mala.status_code == 422 and len(mala.json()["errores"]) == 4
    ok = cli.put(f"{base}/periodos/{pid}/novedades", headers=H(clave), json=[
        {"documento": "45678912", "codigo": "@HEXT_25", "valor": "4"},
        {"documento": "45678912", "codigo": "@COMISIONES", "valor": "350.50"}])
    assert ok.json()["guardadas"] == 2

    r = cli.post(f"{base}/planillas/{pid}/recalcular", headers=H(clave))
    assert r.status_code == 200, r.text
    assert r.json()["colaboradores"] == 2

    filas = {f["documento"]: f for f in cli.get(f"{base}/planillas/{pid}/resultados", headers=H(clave)).json()["filas"]}
    assert filas["45678912"]["dias_trabajados"] == 29          # solo la falta de octubre
    assert filas["40123456"]["dias_trabajados"] == 10          # ingreso el día 21
    d_luis = cli.get(f"{base}/planillas/{pid}/resultados/{luis}/detalle", headers=H(clave)).json()
    haber = [c for c in d_luis["conceptos"] if c["codigo"] == "@HABER_BASICO"][0]
    assert float(haber["monto"]) == 1000.00 and "10" in haber["formula"]
    assert all(c["codigo"] != "@SNP_ONP" or float(c["monto"]) == 130.00 for c in d_luis["conceptos"])
    for f in filas.values():
        assert round(float(f["ingresos"]) - float(f["descuentos"]), 2) == round(float(f["neto"]), 2)

    # recalcular de nuevo no duplica ni pierde novedades
    cli.post(f"{base}/planillas/{pid}/recalcular", headers=H(clave))
    assert len(cli.get(f"{base}/planillas/{pid}/resultados", headers=H(clave)).json()["filas"]) == 2

    # exportaciones
    afp = cli.get(f"{base}/planillas/{pid}/exportar-afpnet?formato=csv", headers=H(clave))
    assert afp.status_code == 200 and afp.text.count("\n") == 1          # solo Ana está en AFP
    z = cli.get(f"{base}/planillas/{pid}/exportar-plame-zip", headers=H(clave))
    nombres = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert sorted(n.rsplit(".", 1)[1] for n in nombres) == ["jor", "rem", "snl"]
    snl = zipfile.ZipFile(io.BytesIO(z.content)).read(nombres[[n.endswith("snl") for n in nombres].index(True)]).decode("latin-1")
    assert snl.strip() == "01|45678912|06|1|"                          # solo la falta de octubre

    # cierre: inmutable
    assert cli.post(f"{base}/planillas/{pid}/cerrar", headers=H(clave)).status_code == 200
    assert cli.post(f"{base}/planillas/{pid}/recalcular", headers=H(clave)).status_code == 403
    assert cli.put(f"{base}/periodos/{pid}/novedades", headers=H(clave),
                   json=[{"documento": "45678912", "codigo": "@COMISIONES", "valor": "1"}]).status_code == 403
    assert cli.post(f"{base}/planillas/{pid}/reabrir", headers=H(clave), json={"motivo": "ok"}).status_code == 422
    assert cli.post(f"{base}/planillas/{pid}/reabrir", headers=H(clave), json={"motivo": "Corrección de comisión"}).status_code == 200
    assert cli.post(f"{base}/planillas/{pid}/recalcular", headers=H(clave)).status_code == 200


def test_aislamiento_entre_cuentas(entorno):
    cli, nueva = entorno
    a, b = nueva("A"), nueva("B")
    eid, *_ = _preparar(cli, a)
    assert cli.get(f"/api/v1/empresas/{eid}/empleados", headers=H(b)).status_code == 404
    assert cli.get(f"/api/v1/empresas/{eid}/empleados").status_code == 401
    assert cli.get(f"/api/v1/empresas/{eid}/empleados", headers=H("pa_falsa")).status_code == 401


def test_limite_de_plan_y_dni_por_empresa(entorno):
    cli, nueva = entorno
    clave = nueva("Gratis", max_empresas=1, max_trabajadores=2)
    eid, *_ = _preparar(cli, clave)
    r = cli.post(f"/api/v1/empresas/{eid}/empleados", headers=H(clave), json={
        "nombres": "Tercero", "apellidos": "Extra", "documento_identidad": "11111111",
        "fecha_ingreso": "2026-10-01", "sueldo_basico": 2000})
    assert r.status_code == 403
    assert cli.post("/api/v1/empresas", json={"ruc": "20222222222", "razon_social": "Otra SAC"}, headers=H(clave)).status_code == 403


def test_mismo_dni_en_dos_empresas_distintas(entorno):
    cli, nueva = entorno
    clave = nueva("Freelancer", max_empresas=2)
    eid, *_ = _preparar(cli, clave)
    e2 = cli.post("/api/v1/empresas", json={"ruc": "20333333333", "razon_social": "Cliente Dos"}, headers=H(clave)).json()["id"]
    r = cli.post(f"/api/v1/empresas/{e2}/empleados", headers=H(clave), json={
        "nombres": "Ana", "apellidos": "Quispe", "documento_identidad": "45678912",
        "fecha_ingreso": "2026-10-01", "sueldo_basico": 2000})
    assert r.status_code == 201


def test_sin_sueldo_vigente_da_error_claro(entorno):
    cli, nueva = entorno
    clave = nueva("F")
    eid, ana, luis, pid = _preparar(cli, clave)
    pid2 = cli.post(f"/api/v1/empresas/{eid}/periodos", json={"anio": 2026, "mes": 9}, headers=H(clave)).json()["id"]
    r = cli.post(f"/api/v1/empresas/{eid}/planillas/{pid2}/recalcular", headers=H(clave))
    assert r.status_code in (200, 409)   # Luis aún no ingresaba en setiembre; Ana sí tiene sueldo


def test_endpoints_de_apoyo_al_frontend(entorno):
    cli, nueva = entorno
    clave = nueva("F")
    eid, ana, luis, pid = _preparar(cli, clave)
    b = f"/api/v1/empresas/{eid}"
    cli.post(f"{b}/empleados/{ana}/ausentismos", headers=H(clave), json={"codigo_plame": "22", "fecha_inicio": "2026-10-08", "fecha_fin": "2026-10-09"})
    cli.post(f"{b}/empleados/{ana}/vacaciones", headers=H(clave), json={"fecha_inicio": "2026-10-14", "fecha_fin": "2026-10-20"})
    aus = cli.get(f"{b}/empleados/{ana}/ausentismos", headers=H(clave)).json()
    assert aus[0]["dias"] == 2 and aus[0]["concepto"] == "@DIAS_DMEDICO"
    assert cli.get(f"{b}/empleados/{ana}/vacaciones", headers=H(clave)).json()[0]["dias_gozados"] == 7
    # PATCH del legajo
    assert cli.patch(f"{b}/empleados/{ana}", headers=H(clave), json={"cobertura_eps": True, "banco": "BCP"}).status_code == 200
    assert cli.patch(f"{b}/empleados/{ana}", headers=H(clave), json={"afp_nombre": "NOEXISTE"}).status_code == 422
    lista = {e["documento"]: e for e in cli.get(f"{b}/empleados", headers=H(clave)).json()}
    assert lista["45678912"]["cobertura_eps"] is True and lista["45678912"]["asignacion_familiar"] is True
    # conceptos cargables: no incluyen los calculados
    cod = {c["codigo"] for c in cli.get(f"{b}/diccionario/conceptos", headers=H(clave)).json()}
    assert "@COMISIONES" in cod and "@HABER_BASICO" not in cod
    # novedades: alta, listado, borrado y resumen
    cli.put(f"{b}/periodos/{pid}/novedades", headers=H(clave), json=[{"documento": "45678912", "codigo": "@COMISIONES", "valor": "100"}])
    nov = cli.get(f"{b}/periodos/{pid}/novedades", headers=H(clave)).json()
    assert len(nov) == 1 and nov[0]["descripcion"] == "Comisiones"
    assert cli.delete(f"{b}/periodos/{pid}/novedades/{nov[0]['id']}", headers=H(clave)).status_code == 200
    cli.post(f"{b}/planillas/{pid}/recalcular", headers=H(clave))
    res = cli.get(f"{b}/planillas/{pid}/resumen-conceptos", headers=H(clave)).json()
    assert any(c["codigo"] == "@HABER_BASICO" for c in res["conceptos"]) and float(res["neto_total"]) > 0


def test_parametros_del_diccionario_visibles(entorno):
    cli, nueva = entorno
    clave = nueva("F")
    eid, *_ = _preparar(cli, clave)
    r = cli.get(f"/api/v1/empresas/{eid}/diccionario/parametros", headers=H(clave)).json()
    assert r["diccionario"] == "RG_728 v1" and r["parametros"]["UIT"].startswith("5500")
    assert [a["nombre"] for a in r["afps"]] == ["HABITAT", "INTEGRA", "PRIMA", "PROFUTURO"]


def test_pagina_de_alta_inicial_sin_consola(entorno, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.delenv("SETUP_TOKEN", raising=False)
    with TestClient(app) as c:                     # el arranque crea tablas y siembra el Diccionario solo
        assert c.get("/setup").status_code == 404   # sin SETUP_TOKEN la página no existe
        monkeypatch.setenv("SETUP_TOKEN", "mi-codigo-secreto")
        assert c.get("/setup").status_code == 200
        assert c.post("/setup", data={"token": "malo", "nombre": "X", "plan": "GRATIS"}).status_code == 403
        r = c.post("/setup", data={"token": "mi-codigo-secreto", "nombre": "Estudio Demo", "plan": "GRATIS"})
        assert r.status_code == 200 and "pa_" in r.text
        clave = r.text.split("pa_", 1)[1].split("<", 1)[0]
        creada = c.post("/api/v1/empresas", headers=H("pa_" + clave), json={"ruc": "20999999999", "razon_social": "Cliente Auto SAC"})
        assert creada.status_code == 201, creada.text


def test_health_informa_la_version_para_diagnosticar_despliegues(entorno):
    cli, _ = entorno
    h = cli.get("/api/v1/health").json()
    assert h["status"] == "ok" and h["version"] == "2026-11-11-f5" and h["base"] in ("sqlite", "postgresql")
