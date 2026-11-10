"""La quincena es un ADELANTO configurable por empresa: proceso propio (no calcula aportes ni 5ta) que la planilla de fin de mes descuenta."""
from decimal import Decimal as D

H = lambda k: {"X-API-Key": k}
API = "/api/v1/empresas"


def _empresa(cli, nueva, usa=True, **cfg):
    clave = nueva("Estudio", max_empresas=3)
    e = cli.post(API, json={"ruc": "20444444444", "razon_social": "Quincena SAC", "usa_quincena": usa, **cfg}, headers=H(clave)).json()["id"]
    B = f"{API}/{e}"
    mk = lambda dni, n, ing, sueldo, afp=False: cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": n, "apellidos": n + " Prueba", "documento_identidad": dni, "fecha_ingreso": ing, "sueldo_basico": sueldo,
        "sistema_pensionario": "AFP" if afp else "ONP", **({"afp_nombre": "PRIMA", "cuspp": "123456ABCDE1"} if afp else {}), "numero_cuenta": "1"}).json()["id"]
    return clave, B, mk


def _det(cli, clave, B, pid, emp):
    return {c["codigo"]: c for c in cli.get(f"{B}/planillas/{pid}/resultados/{emp}/detalle", headers=H(clave)).json()["conceptos"]}


def test_la_quincena_se_configura_por_empresa(entorno):
    cli, nueva = entorno
    clave, B, mk = _empresa(cli, nueva, usa=False)
    e = cli.get(API, headers=H(clave)).json()[0]
    assert e["usa_quincena"] is False and e["quincena_modo"] == "PORCENTAJE" and D(str(e["quincena_valor"])) == D("50")
    # una empresa que no usa quincena no puede crear ni procesar ese periodo
    r = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10, "fase": "Quincena"}, headers=H(clave))
    assert r.status_code == 409 and "Datos de la empresa" in r.json()["detail"]
    assert cli.patch(B, headers=H(clave), json={"usa_quincena": True, "quincena_modo": "PORCENTAJE", "quincena_valor": 150}).status_code == 422
    assert cli.patch(B, headers=H(clave), json={"quincena_modo": "OTRO"}).status_code == 422
    assert cli.patch(B, headers=H(clave), json={"usa_quincena": True, "quincena_modo": "PORCENTAJE", "quincena_valor": 40}).status_code == 200
    e = cli.get(API, headers=H(clave)).json()[0]
    assert e["usa_quincena"] is True and D(str(e["quincena_valor"])) == D("40")
    assert cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10, "fase": "Quincena"}, headers=H(clave)).status_code == 201
    # también se configura al registrar la empresa
    c2 = cli.post(API, json={"ruc": "20555555555", "razon_social": "Otra SAC", "usa_quincena": True, "quincena_modo": "MONTO", "quincena_valor": 500}, headers=H(clave)).json()["id"]
    e2 = [x for x in cli.get(API, headers=H(clave)).json() if x["id"] == c2][0]
    assert (e2["usa_quincena"], e2["quincena_modo"], D(str(e2["quincena_valor"]))) == (True, "MONTO", D("500"))
    assert cli.post(API, json={"ruc": "20666666666", "razon_social": "X SAC", "quincena_modo": "PORCENTAJE", "quincena_valor": 101}, headers=H(clave)).status_code == 422


def test_proceso_de_quincena_genera_solo_adelantos(entorno):
    cli, nueva = entorno
    clave, B, mk = _empresa(cli, nueva)
    ana = mk("45678912", "Ana", "2026-01-01", 3000, afp=True)
    tarde = mk("40123456", "Tarde", "2026-10-20", 2000)
    cesado = mk("70112233", "Cesado", "2026-01-01", 2000)
    cli.post(f"{B}/empleados/{cesado}/baja", headers=H(clave), json={"fecha_cese": "2026-10-10", "motivo": "01"})
    q = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10, "fase": "Quincena"}, headers=H(clave)).json()["id"]
    r = cli.post(f"{B}/planillas/{q}/recalcular", headers=H(clave)).json()
    assert r["tipo"] == "ADELANTO" and r["colaboradores"] == 1
    assert any("TARDE" in a and "después del 15" in a for a in r["alertas"]) and any("CESADO" in a and "antes del 15" in a for a in r["alertas"])
    assert any("adelanto: no calcula aportes ni 5ta" in a for a in r["alertas"])
    det = _det(cli, clave, B, q, ana)
    assert set(det) == {"@ADELANTO_QUINCENA"} and D(str(det["@ADELANTO_QUINCENA"]["monto"])) == D("1500.00")          # 50 % de 3,000
    fila = cli.get(f"{B}/planillas/{q}/resultados", headers=H(clave)).json()["filas"]
    assert [f["documento"] for f in fila] == ["45678912"] and D(str(fila[0]["neto"])) == D("1500.00") and D(str(fila[0]["descuentos"])) == 0
    # modos configurables: otro porcentaje y monto fijo
    cli.patch(B, headers=H(clave), json={"quincena_valor": 30})
    cli.post(f"{B}/planillas/{q}/recalcular", headers=H(clave))
    assert D(str(_det(cli, clave, B, q, ana)["@ADELANTO_QUINCENA"]["monto"])) == D("900.00")
    cli.patch(B, headers=H(clave), json={"quincena_modo": "MONTO", "quincena_valor": 500})
    cli.post(f"{B}/planillas/{q}/recalcular", headers=H(clave))
    assert D(str(_det(cli, clave, B, q, ana)["@ADELANTO_QUINCENA"]["monto"])) == D("500.00")


def test_la_planilla_de_fin_de_mes_descuenta_la_quincena(entorno):
    cli, nueva = entorno
    clave, B, mk = _empresa(cli, nueva)
    ana = mk("45678912", "Ana", "2026-01-01", 3000, afp=True)
    fm = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10}, headers=H(clave)).json()["id"]
    cli.post(f"{B}/planillas/{fm}/recalcular", headers=H(clave))
    sin = _det(cli, clave, B, fm, ana)
    neto_sin = D(str(cli.get(f"{B}/planillas/{fm}/resultados", headers=H(clave)).json()["filas"][0]["neto"]))
    assert "@QUINCENA" not in sin
    q = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10, "fase": "Quincena"}, headers=H(clave)).json()["id"]
    cli.post(f"{B}/planillas/{q}/recalcular", headers=H(clave))
    cli.post(f"{B}/planillas/{fm}/recalcular", headers=H(clave))
    con = _det(cli, clave, B, fm, ana)
    assert con["@QUINCENA"]["categoria"] == "DESCUENTO" and D(str(con["@QUINCENA"]["monto"])) == D("1500.00")
    neto_con = D(str(cli.get(f"{B}/planillas/{fm}/resultados", headers=H(clave)).json()["filas"][0]["neto"]))
    assert neto_con == neto_sin - D("1500.00")                                              # solo cambia el neto: aportes y 5ta no se tocan
    assert D(str(con["@FONDO_PENSIONES"]["monto"])) == D(str(sin["@FONDO_PENSIONES"]["monto"]))
    # una novedad manual de @QUINCENA tiene prioridad sobre el adelanto automático
    cli.put(f"{B}/periodos/{fm}/novedades", headers=H(clave), json=[{"documento": "45678912", "codigo": "@QUINCENA", "valor": "700"}])
    cli.post(f"{B}/planillas/{fm}/recalcular", headers=H(clave))
    man = _det(cli, clave, B, fm, ana)
    assert D(str(man["@QUINCENA"]["monto"])) == D("700.00") and man["@QUINCENA"]["origen"] == "NOVEDAD"


def test_la_quincena_no_genera_afpnet_plame_ni_boletas(entorno):
    cli, nueva = entorno
    clave, B, mk = _empresa(cli, nueva)
    mk("45678912", "Ana", "2026-01-01", 3000)
    q = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10, "fase": "Quincena"}, headers=H(clave)).json()["id"]
    cli.post(f"{B}/planillas/{q}/recalcular", headers=H(clave))
    for ruta in ("exportar-afpnet", "exportar-plame-zip", "boletas/pdf"):
        r = cli.get(f"{B}/planillas/{q}/{ruta}", headers=H(clave))
        assert r.status_code == 409 and "adelanto" in r.json()["detail"], ruta
    fm = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10}, headers=H(clave)).json()["id"]
    cli.post(f"{B}/planillas/{fm}/recalcular", headers=H(clave))
    assert cli.get(f"{B}/planillas/{fm}/exportar-afpnet", headers=H(clave)).status_code == 200


def test_si_la_empresa_desactiva_la_quincena_no_se_puede_procesar(entorno):
    cli, nueva = entorno
    clave, B, mk = _empresa(cli, nueva)
    mk("45678912", "Ana", "2026-01-01", 3000)
    q = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10, "fase": "Quincena"}, headers=H(clave)).json()["id"]
    cli.patch(B, headers=H(clave), json={"usa_quincena": False})
    r = cli.post(f"{B}/planillas/{q}/recalcular", headers=H(clave))
    assert r.status_code == 409 and "no tiene activada la quincena" in r.json()["detail"]
