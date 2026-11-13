"""Parámetros y tasas AFP por periodo (heredan al abrir el siguiente), afectaciones y suspensiones como dato único, y catálogos con periodo de aplicación."""
from decimal import Decimal as D

from tests.test_liquidacion import API, H, _escenario

V = lambda B, fam: f"{B}/config/vigencias/{fam}"


def _tot(cli, clave, B, pid):
    r = cli.get(f"{B}/planillas/{pid}/resumen-conceptos", headers=H(clave)).json()["conceptos"]
    return {c["codigo"]: D(str(c["total"])) for c in r}


def _recalc(cli, clave, B, pid):
    assert cli.post(f"{B}/planillas/{pid}/recalcular", headers=H(clave)).status_code == 200


def _reg(cli, apikey, B, fam, **k):
    return cli.post(V(B, fam), headers=H(apikey), json=k)


PU = lambda B, fam: f"{B}/config/unico/{fam}"
TASAS = ("aporte", "comision", "comision_mixta_flujo", "comision_saldo", "prima")


def _params(cli, clave, B, pid):
    return cli.get(f"{B}/periodos/{pid}/parametros", headers=H(clave)).json()


def test_los_parametros_se_editan_por_periodo_y_cambian_la_planilla_que_se_procese(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    p = _params(cli, clave, B, pids[9])
    assert p["editable"] is True and D(p["parametros"]["UIT"]) == D("5500") and D(p["parametros"]["ASIG_FAM_PCT"]) == D("0.10")
    assert _tot(cli, clave, B, pids[9])["@ASIGNACION_FAMILIAR"] == D("113.00")
    r = cli.put(f"{B}/periodos/{pids[9]}/parametros", headers=H(clave), json={"parametros": {**p["parametros"], "ASIG_FAM_PCT": "0.20"}, "afps": p["afps"]})
    assert r.status_code == 200 and D(r.json()["parametros"]["ASIG_FAM_PCT"]) == D("0.20")
    assert _tot(cli, clave, B, pids[9])["@ASIGNACION_FAMILIAR"] == D("113.00")                      # no cambia nada solo: rige al procesar
    _recalc(cli, clave, B, pids[9])
    assert _tot(cli, clave, B, pids[9])["@ASIGNACION_FAMILIAR"] == D("226.00")
    assert D(_params(cli, clave, B, pids[7])["parametros"]["ASIG_FAM_PCT"]) == D("0.10")           # los otros periodos conservan los suyos
    _recalc(cli, clave, B, pids[7])
    assert _tot(cli, clave, B, pids[7])["@ASIGNACION_FAMILIAR"] == D("113.00")


def test_la_informacion_viaja_al_abrir_el_siguiente_periodo(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    p = _params(cli, clave, B, pids[10])
    afps = [{**a, "comision": "0.0200"} if a["nombre"] == "PRIMA" else a for a in p["afps"]]
    assert cli.put(f"{B}/periodos/{pids[10]}/parametros", headers=H(clave), json={"parametros": {**p["parametros"], "UIT": "5600", "RMV": "1200"}, "afps": afps}).status_code == 200
    nuevo = cli.post(f"{B}/periodos", headers=H(clave), json={"anio": 2026, "mes": 11})
    assert nuevo.status_code == 201 and nuevo.json()["parametros_arrastrados"] is True
    n = _params(cli, clave, B, nuevo.json()["id"])
    assert D(n["parametros"]["UIT"]) == D("5600") and D(n["parametros"]["RMV"]) == D("1200")
    assert D({a["nombre"]: a for a in n["afps"]}["PRIMA"]["comision"]) == D("0.02")                # la tasa AFP también viaja
    assert D(_params(cli, clave, B, pids[9])["parametros"]["UIT"]) == D("5500")                    # y el periodo anterior no se toca
    nov = nuevo.json()["id"]
    assert cli.put(f"{B}/periodos/{nov}/parametros", headers=H(clave), json={"parametros": {**n["parametros"], "UIT": "5700"}, "afps": n["afps"]}).status_code == 200
    assert D(_params(cli, clave, B, pids[10])["parametros"]["UIT"]) == D("5600")                   # editar el nuevo tampoco cambia el anterior


def test_validaciones_y_periodo_cerrado_de_los_parametros(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    p = _params(cli, clave, B, pids[9])
    put = lambda pid, **cambios: cli.put(f"{B}/periodos/{pid}/parametros", headers=H(clave), json={"parametros": {**p["parametros"], **cambios}, "afps": p["afps"]}).status_code
    assert put(pids[9], UIT="5600") == 200 and put(pids[9], UIT="abc") == 422 and put(pids[9], UIT="-5") == 422
    assert put(pids[9], ESSALUD_TASA="9") == 422 and put(pids[9], BASE_DIAS="30") == 422           # una tasa va entre 0 y 1; BASE_DIAS no se edita
    malas = [{**a, "prima": "2"} if a["nombre"] == "PRIMA" else a for a in p["afps"]]
    assert cli.put(f"{B}/periodos/{pids[9]}/parametros", headers=H(clave), json={"parametros": p["parametros"], "afps": malas}).status_code == 422
    assert cli.post(f"{B}/planillas/{pids[8]}/cerrar", headers=H(clave)).status_code == 200
    assert _params(cli, clave, B, pids[8])["editable"] is False and put(pids[8], UIT="5700") == 403
    assert cli.post(f"{B}/planillas/{pids[8]}/reabrir", headers=H(clave), json={"motivo": "corregir un valor"}).status_code == 200
    assert put(pids[8], UIT="5700") == 200                                                          # reabierto, vuelve a poder editarse


def test_la_tasa_afp_del_periodo_cambia_los_descuentos(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    antes = _tot(cli, clave, B, pids[9])
    assert antes["@FONDO_PENSIONES"] == D("311.30") and antes["@COMISION_VARIABLE"] == D("49.81")        # sobre 3,113.00
    p = _params(cli, clave, B, pids[9])
    afps = [{**a, "comision": "0.0200"} if a["nombre"] == "PRIMA" else a for a in p["afps"]]
    assert cli.put(f"{B}/periodos/{pids[9]}/parametros", headers=H(clave), json={"parametros": p["parametros"], "afps": afps}).status_code == 200
    _recalc(cli, clave, B, pids[9])
    assert _tot(cli, clave, B, pids[9])["@COMISION_VARIABLE"] == D("62.26")                          # 2.00 % de 3,113.00


def test_afectaciones_dato_unico_rigen_para_la_planilla_que_se_procese(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    lst = cli.get(PU(B, "AFECTACION"), headers=H(clave)).json()
    it = {i["clave"]: i for i in lst["items"]}["@ASIGNACION_FAMILIAR"]
    assert it["base"]["afp"] is True and it["modificado"] is False and "historial" not in lst
    r = cli.put(PU(B, "AFECTACION"), headers=H(clave), json={"valores": {"@ASIGNACION_FAMILIAR": {"afp": False, "essalud": True, "quinta": True}}})
    assert r.status_code == 200 and {i["clave"]: i for i in r.json()["items"]}["@ASIGNACION_FAMILIAR"]["modificado"] is True
    assert _tot(cli, clave, B, pids[9])["@FONDO_PENSIONES"] == D("311.30")                           # nada cambia hasta procesar
    _recalc(cli, clave, B, pids[9])
    t = _tot(cli, clave, B, pids[9])
    assert t["@FONDO_PENSIONES"] == D("300.00") and t["@COMISION_VARIABLE"] == D("48.00")           # base 3,000.00 sin la asignación (1.60 %)
    _recalc(cli, clave, B, pids[10])
    assert _tot(cli, clave, B, pids[10])["@FONDO_PENSIONES"] != D("311.30")                          # rige también para otro periodo abierto que se procese
    # volver al valor del Diccionario quita el registro propio
    r = cli.put(PU(B, "AFECTACION"), headers=H(clave), json={"valores": {"@ASIGNACION_FAMILIAR": it["base"]}})
    assert {i["clave"]: i for i in r.json()["items"]}["@ASIGNACION_FAMILIAR"]["modificado"] is False
    _recalc(cli, clave, B, pids[9])
    assert _tot(cli, clave, B, pids[9])["@FONDO_PENSIONES"] == D("311.30")


def test_suspensiones_dato_unico_y_validaciones(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    items = cli.get(PU(B, "SUSPENSION"), headers=H(clave)).json()["items"]
    base = [i for i in items if i["base"]["tipo"] == "IMPERFECTA"][0]
    r = cli.put(PU(B, "SUSPENSION"), headers=H(clave), json={"valores": {base["clave"]: {"tipo": "PERFECTA"}}})
    assert r.status_code == 200 and {i["clave"]: i for i in r.json()["items"]}[base["clave"]]["vigente"]["tipo"] == "PERFECTA"
    assert cli.put(PU(B, "SUSPENSION"), headers=H(clave), json={"valores": {base["clave"]: {"tipo": "OTRA"}}}).status_code == 422
    assert cli.put(PU(B, "SUSPENSION"), headers=H(clave), json={"valores": {"99": {"tipo": "PERFECTA"}}}).status_code == 422                   # no existe en el Diccionario
    assert cli.put(PU(B, "AFECTACION"), headers=H(clave), json={"valores": {"@FONDO_PENSIONES": {"afp": True}}}).status_code == 422             # un descuento no tiene afectaciones
    assert cli.get(PU(B, "NADA"), headers=H(clave)).status_code == 404 and cli.get(PU(B, "PARAMETRO"), headers=H(clave)).status_code == 404
    # ya no se registran con periodo de aplicación
    body = {"clave": "UIT", "valor": {"valor": "5600"}, "aplica_desde": "2026-09"}
    assert cli.post(V(B, "PARAMETRO"), headers=H(clave), json=body).status_code == 409
    assert cli.post(V(B, "TASA_AFP"), headers=H(clave), json=body).status_code == 409
    assert cli.post(V(B, "SUSPENSION"), headers=H(clave), json={**body, "clave": base["clave"], "valor": {"tipo": "PERFECTA"}}).status_code == 409


CT = lambda B, fam: f"{B}/config/catalogo/{fam}"


def test_catalogos_son_tablas_con_registro_edicion_y_borrado(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    ver = lambda pid: cli.get(f"{B}/config/catalogos/verificar?periodo_id={pid}", headers=H(clave)).json()["problemas"]
    assert ver(pids[9]) == [] and cli.get(CT(B, "CENTRO_COSTO"), headers=H(clave)).json()["items"] == []     # sin catálogo registrado no se revisa nada
    r = cli.post(CT(B, "CENTRO_COSTO"), headers=H(clave), json={"codigo": "000001101", "nombre": "ADMINISTRATIVO"})
    assert r.status_code == 201 and r.json()["activo"] is True
    assert cli.post(CT(B, "CENTRO_COSTO"), headers=H(clave), json={"codigo": "000001101", "nombre": "OTRO"}).status_code == 409        # código repetido
    assert cli.post(CT(B, "PUESTO"), headers=H(clave), json={"codigo": "X", "nombre": ""}).status_code == 422
    assert cli.post(CT(B, "NADA"), headers=H(clave), json={"codigo": "X", "nombre": "Y"}).status_code == 404
    lst = cli.get(CT(B, "CENTRO_COSTO"), headers=H(clave)).json()
    assert set(lst) == {"familia", "items"} and lst["items"][0]["en_uso"] == 1                                                      # sin historial; Ana lo usa
    assert [(p["colaborador"].split(",")[0], p["motivo"]) for p in ver(pids[9])] == [("ROJAS DIAZ", "sin asignar")]                    # Ana coincide con «000001101 ADMINISTRATIVO»
    rid = r.json()["id"]
    e = cli.put(f"{CT(B, 'CENTRO_COSTO')}/{rid}", headers=H(clave), json={"nombre": "ADMINISTRATIVO", "activo": False})
    assert e.status_code == 200 and e.json()["activo"] is False and e.json()["codigo"] == "000001101"
    assert {(p["colaborador"].split(",")[0], p["motivo"]) for p in ver(pids[9])} == {("QUISPE ROJAS", "inactivo en el catálogo"), ("ROJAS DIAZ", "sin asignar")}
    cli.put(f"{CT(B, 'CENTRO_COSTO')}/{rid}", headers=H(clave), json={"nombre": "ADMINISTRATIVO", "activo": True})
    assert [p for p in ver(pids[9]) if p["colaborador"].startswith("QUISPE")] == []
    # áreas: un código que no coincide con lo que tiene el colaborador
    assert cli.post(CT(B, "AREA"), headers=H(clave), json={"codigo": "LOG", "nombre": "LOGISTICA"}).status_code == 201
    assert any(p["familia"] == "AREA" and p["motivo"] == "no existe en el catálogo" for p in ver(pids[9]))
    # borrar quita el registro; se puede crear de nuevo
    assert cli.delete(f"{CT(B, 'CENTRO_COSTO')}/{rid}", headers=H(clave)).status_code == 200 and cli.delete(f"{CT(B, 'CENTRO_COSTO')}/{rid}", headers=H(clave)).status_code == 404
    assert cli.get(CT(B, "CENTRO_COSTO"), headers=H(clave)).json()["items"] == []
    assert cli.post(CT(B, "CENTRO_COSTO"), headers=H(clave), json={"codigo": "000001101", "nombre": "ADMINISTRATIVO"}).status_code == 201
    # ya no llevan periodo de aplicación
    body = {"clave": "LOG2", "valor": {"nombre": "X"}, "aplica_desde": "2026-09"}
    assert cli.post(V(B, "AREA"), headers=H(clave), json=body).status_code == 409
