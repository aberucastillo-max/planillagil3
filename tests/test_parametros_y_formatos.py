"""Comisión por saldo y tipo de comisión del trabajador, parámetros editables por periodo (arrastre mes a mes), boleta PDF y datos de empresa."""
from decimal import Decimal as D

from app.letras import monto_en_letras
from tests.test_liquidacion import API, H, _cerrar, _conceptos, _escenario, _vista

DET = lambda cli, clave, B, pid, emp: {c["codigo"]: c for c in cli.get(f"{B}/planillas/{pid}/resultados/{emp}/detalle", headers=H(clave)).json()["conceptos"]}


def _empresa(cli, nueva):
    clave = nueva("Estudio", max_empresas=2)
    e = cli.post(API, json={"ruc": "20555555555", "razon_social": "Parametros SAC"}, headers=H(clave)).json()["id"]
    return clave, f"{API}/{e}"


def _emp(cli, clave, B, dni, nombre, tipo="FLUJO", afp="INTEGRA"):
    return cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": nombre, "apellidos": nombre + " Prueba", "documento_identidad": dni, "fecha_ingreso": "2026-01-01", "sueldo_basico": 3000,
        "sistema_pensionario": "AFP", "afp_nombre": afp, "cuspp": "123456ABCDE1", "numero_cuenta": "1", "tipo_comision": tipo}).json()["id"]


def test_monto_en_letras_como_el_recibo():
    assert monto_en_letras("11752.09") == "ONCE MIL SETECIENTOS CINCUENTA Y DOS CON 09/100 SOLES"
    assert monto_en_letras("21000") == "VEINTIUN MIL CON 00/100 SOLES" and monto_en_letras("100") == "CIEN CON 00/100 SOLES"


def test_tasas_oficiales_incluyen_la_comision_por_saldo(entorno):
    cli, nueva = entorno
    clave, B = _empresa(cli, nueva)
    pid = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10}, headers=H(clave)).json()["id"]
    r = cli.get(f"{B}/periodos/{pid}/parametros", headers=H(clave)).json()
    assert r["origen"] == "DICCIONARIO" and r["editable"] and r["parametros"]["UIT"].startswith("5500")
    saldo = {a["nombre"]: D(a["comision_saldo"]) for a in r["afps"]}
    assert saldo == {"HABITAT": D("0.0125"), "INTEGRA": D("0.0078"), "PRIMA": D("0.0125"), "PROFUTURO": D("0.0068")}
    assert all(D(a["comision_mixta_flujo"]) == 0 for a in r["afps"])                      # el componente mensual de la mixta es 0 %


def test_el_tipo_de_comision_del_trabajador_define_lo_que_se_descuenta(entorno):
    cli, nueva = entorno
    clave, B = _empresa(cli, nueva)
    flujo, mixta = _emp(cli, clave, B, "45678912", "Flujo"), _emp(cli, clave, B, "40123456", "Mixta", tipo="MIXTA")
    pid = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10}, headers=H(clave)).json()["id"]
    cli.post(f"{B}/planillas/{pid}/recalcular", headers=H(clave))
    f, m = DET(cli, clave, B, pid, flujo), DET(cli, clave, B, pid, mixta)
    assert D(str(f["@COMISION_VARIABLE"]["monto"])) == D("46.50")                           # 1.55 % de 3,000
    assert "@COMISION_VARIABLE" not in m                                                    # mixta: por saldo, la cobra la AFP
    assert D(str(m["@FONDO_PENSIONES"]["monto"])) == D(str(f["@FONDO_PENSIONES"]["monto"])) == D("300.00")
    assert D(str(m["@SEGURO_INVALIDEZ"]["monto"])) == D("41.10")
    # cambiar el tipo en el legajo se refleja en la siguiente planilla
    assert cli.patch(f"{B}/empleados/{flujo}", headers=H(clave), json={"tipo_comision": "MIXTA"}).status_code == 200
    assert cli.patch(f"{B}/empleados/{flujo}", headers=H(clave), json={"tipo_comision": "OTRA"}).status_code == 422
    cli.post(f"{B}/planillas/{pid}/recalcular", headers=H(clave))
    assert "@COMISION_VARIABLE" not in DET(cli, clave, B, pid, flujo)
    lista = {e["documento"]: e for e in cli.get(f"{B}/empleados", headers=H(clave)).json()}
    assert lista["45678912"]["tipo_comision"] == "MIXTA"


def test_comision_mixta_tambien_en_la_liquidacion(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    assert "@COMISION_VARIABLE" in _conceptos(_vista(cli, clave, B, pids[10], ana, True))
    cli.patch(f"{B}/empleados/{ana}", headers=H(clave), json={"tipo_comision": "MIXTA"})
    v = _vista(cli, clave, B, pids[10], ana, True)
    assert "@COMISION_VARIABLE" not in _conceptos(v) and v["snapshot"]["pension"]["tipo_comision"] == "MIXTA"
    assert D(v["snapshot"]["pension"]["comision_saldo_pct"]) == D("1.25")


def test_parametros_editables_por_periodo_y_arrastre_al_mes_siguiente(entorno):
    cli, nueva = entorno
    clave, B = _empresa(cli, nueva)
    ana = _emp(cli, clave, B, "45678912", "Ana", afp="PRIMA")
    cli.patch(f"{B}/empleados/{ana}", headers=H(clave), json={"asignacion_familiar": True})
    p10 = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10}, headers=H(clave)).json()["id"]
    cli.post(f"{B}/planillas/{p10}/recalcular", headers=H(clave))
    assert D(str(DET(cli, clave, B, p10, ana)["@ASIGNACION_FAMILIAR"]["monto"])) == D("113.00")           # 10 % de la RMV 1,130
    r = cli.put(f"{B}/periodos/{p10}/parametros", headers=H(clave), json={
        "parametros": {"RMV": "1300", "UIT": "5600"},
        "afps": [{"nombre": "PRIMA", "aporte": "0.10", "comision": "0.0160", "comision_mixta_flujo": "0", "comision_saldo": "0.0130", "prima": "0.0150"}]})
    assert r.status_code == 200 and r.json()["origen"] == "PERIODO" and D(r.json()["parametros"]["RMV"]) == D("1300")
    assert {a["nombre"]: D(a["comision_saldo"]) for a in r.json()["afps"]}["PRIMA"] == D("0.0130")
    cli.post(f"{B}/planillas/{p10}/recalcular", headers=H(clave))
    d = DET(cli, clave, B, p10, ana)
    assert D(str(d["@ASIGNACION_FAMILIAR"]["monto"])) == D("130.00")                                    # usa el valor editado
    assert D(str(d["@SEGURO_INVALIDEZ"]["monto"])) == D("46.95")                                        # 1.50 % de 3,130
    # al abrir el mes siguiente viajan los valores editados
    n = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 11}, headers=H(clave)).json()
    assert n["parametros_arrastrados"] is True
    sig = cli.get(f"{B}/periodos/{n['id']}/parametros", headers=H(clave)).json()
    assert sig["origen"] == "PERIODO" and D(sig["parametros"]["RMV"]) == D("1300")
    # y se pueden volver a editar en ese mes sin tocar el anterior
    cli.put(f"{B}/periodos/{n['id']}/parametros", headers=H(clave), json={"parametros": {"RMV": "1400"}})
    assert D(cli.get(f"{B}/periodos/{p10}/parametros", headers=H(clave)).json()["parametros"]["RMV"]) == D("1300")
    # validaciones y periodo cerrado
    assert cli.put(f"{B}/periodos/{n['id']}/parametros", headers=H(clave), json={"parametros": {"BASE_DIAS": "31"}}).status_code == 422
    assert cli.put(f"{B}/periodos/{n['id']}/parametros", headers=H(clave), json={"parametros": {"ESSALUD_TASA": "9"}}).status_code == 422
    assert cli.put(f"{B}/periodos/{n['id']}/parametros", headers=H(clave), json={"parametros": {"RMV": "abc"}}).status_code == 422
    assert cli.post(f"{B}/planillas/{p10}/cerrar", headers=H(clave)).status_code == 200
    cerrado = cli.put(f"{B}/periodos/{p10}/parametros", headers=H(clave), json={"parametros": {"RMV": "1500"}})
    assert cerrado.status_code == 403 and cli.get(f"{B}/periodos/{p10}/parametros", headers=H(clave)).json()["editable"] is False


def test_boleta_pdf_con_el_formato_de_la_empresa(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    todas = cli.get(f"{B}/planillas/{pids[9]}/boletas/pdf", headers=H(clave))
    assert todas.status_code == 200 and todas.content.startswith(b"%PDF") and "BOLETAS_20111111111_2026_09" in todas.headers["content-disposition"]
    una = cli.get(f"{B}/planillas/{pids[9]}/boletas/pdf?empleado_id={ana}", headers=H(clave))
    assert una.status_code == 200 and "45678912" in una.headers["content-disposition"] and len(una.content) < len(todas.content)
    sin = cli.post(f"{B}/periodos", json={"anio": 2027, "mes": 1}, headers=H(clave)).json()["id"]
    assert cli.get(f"{B}/planillas/{sin}/boletas/pdf", headers=H(clave)).status_code == 409


def test_datos_de_la_empresa_y_del_legajo_para_los_formatos(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    e = cli.get(API, headers=H(clave)).json()[0]
    assert e["direccion_fiscal"] == "AV. PRUEBA 123, LIMA" and e["representante_legal"] == "GERENTE DE PRUEBA"
    assert cli.patch(B, headers=H(clave), json={"direccion_fiscal": "OTRA DIRECCION 456", "cargo_representante": "GERENTE GENERAL"}).status_code == 200
    assert cli.get(API, headers=H(clave)).json()[0]["direccion_fiscal"] == "OTRA DIRECCION 456"
    emp = {x["documento"]: x for x in cli.get(f"{B}/empleados", headers=H(clave)).json()}["45678912"]
    assert (emp["area"], emp["centro_costo"], emp["categoria"], emp["calificacion"]) == ("ADMINISTRACION", "000001101 ADMINISTRATIVO", "EMPLEADOS", "CONFIANZA - NO FISCAL")
    assert cli.patch(f"{B}/empleados/{ana}", headers=H(clave), json={"area": "FINANZAS"}).status_code == 200
