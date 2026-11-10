"""Configuración con periodo de aplicación: parámetros y tasas, afectaciones, suspensiones y catálogos."""
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


def test_un_parametro_con_periodo_de_aplicacion_cambia_la_planilla_desde_ese_mes(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    assert _tot(cli, clave, B, pids[9])["@ASIGNACION_FAMILIAR"] == D("113.00")                      # 10 % de la RMV
    lst = cli.get(V(B, "PARAMETRO"), headers=H(clave)).json()
    item = {i["clave"]: i for i in lst["items"]}["ASIG_FAM_PCT"]
    assert item["origen"] == "DICCIONARIO" and D(item["vigente"]["valor"]) == D("0.10") and lst["historial"] == []
    r = _reg(cli, clave, B, "PARAMETRO", clave="ASIG_FAM_PCT", valor={"valor": "0.20"}, aplica_desde="2026-08", nota="prueba")
    assert r.status_code == 201 and r.json()["aplica_desde"] == "2026-08"
    _recalc(cli, clave, B, pids[7]); _recalc(cli, clave, B, pids[9])
    assert _tot(cli, clave, B, pids[7])["@ASIGNACION_FAMILIAR"] == D("113.00")                      # antes de agosto no cambia
    assert _tot(cli, clave, B, pids[9])["@ASIGNACION_FAMILIAR"] == D("226.00")                      # desde agosto rige el nuevo valor
    en = lambda pid: {i["clave"]: i for i in cli.get(f"{V(B, 'PARAMETRO')}?periodo_id={pid}", headers=H(clave)).json()["items"]}["ASIG_FAM_PCT"]
    assert en(pids[7])["origen"] == "DICCIONARIO" and en(pids[9])["origen"] == "EMPRESA" and en(pids[9])["desde"] == "2026-08"
    # otro valor desde octubre: queda el historial y septiembre no cambia
    assert _reg(cli, clave, B, "PARAMETRO", clave="ASIG_FAM_PCT", valor={"valor": "0.15"}, aplica_desde="2026-10").status_code == 201
    assert en(pids[10])["vigente"]["valor"] == "0.15" and en(pids[9])["vigente"]["valor"] == "0.20"
    assert len(cli.get(V(B, "PARAMETRO"), headers=H(clave)).json()["historial"]) == 2


def test_validaciones_de_la_configuracion(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    p = lambda **k: _reg(cli, clave, B, "PARAMETRO", **{"clave": "UIT", "valor": {"valor": "5600"}, "aplica_desde": "2026-09", **k}).status_code
    assert p() == 201 and p() == 409                                                                  # el mismo mes se rechaza
    assert p(clave="BASE_DIAS", aplica_desde="2026-10") == 422                                        # no es configurable
    assert p(valor={"valor": "abc"}, aplica_desde="2026-10") == 422 and p(valor={"valor": "-5"}, aplica_desde="2026-10") == 422
    assert p(clave="ESSALUD_TASA", valor={"valor": "9"}, aplica_desde="2026-10") == 422               # una tasa va entre 0 y 1
    assert p(aplica_desde="2026-13") == 422
    assert _reg(cli, clave, B, "TASA_AFP", clave="NOEXISTE", valor={c: "0.01" for c in ("aporte", "comision", "comision_mixta_flujo", "comision_saldo", "prima")}, aplica_desde="2026-09").status_code == 422
    assert _reg(cli, clave, B, "AFECTACION", clave="@FONDO_PENSIONES", valor={"afp": True}, aplica_desde="2026-09").status_code == 422   # un descuento no tiene afectaciones
    assert _reg(cli, clave, B, "SUSPENSION", clave="06", valor={"tipo": "OTRA"}, aplica_desde="2026-09").status_code == 422
    assert cli.get(V(B, "NADA"), headers=H(clave)).status_code == 404


def test_tasa_afp_y_afectaciones_cambian_los_descuentos(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    antes = _tot(cli, clave, B, pids[9])
    assert antes["@FONDO_PENSIONES"] == D("311.30") and antes["@COMISION_VARIABLE"] == D("49.81")        # sobre 3,113.00
    tasa = {i["clave"]: i for i in cli.get(V(B, "TASA_AFP"), headers=H(clave)).json()["items"]}["PRIMA"]["base"]
    nueva_tasa = {**tasa, "comision": "0.0200"}
    assert _reg(cli, clave, B, "TASA_AFP", clave="PRIMA", valor=nueva_tasa, aplica_desde="2026-09").status_code == 201
    _recalc(cli, clave, B, pids[9])
    assert _tot(cli, clave, B, pids[9])["@COMISION_VARIABLE"] == D("62.26")                          # 2.00 % de 3,113.00
    # la asignación familiar deja de afectar a la AFP desde septiembre
    assert _reg(cli, clave, B, "AFECTACION", clave="@ASIGNACION_FAMILIAR", valor={"afp": False, "essalud": True, "quinta": True}, aplica_desde="2026-09").status_code == 201
    _recalc(cli, clave, B, pids[9])
    t = _tot(cli, clave, B, pids[9])
    assert t["@FONDO_PENSIONES"] == D("300.00") and t["@COMISION_VARIABLE"] == D("60.00")           # base 3,000.00 sin la asignación
    lst = {i["clave"]: i for i in cli.get(f"{V(B, 'AFECTACION')}?periodo_id={pids[9]}", headers=H(clave)).json()["items"]}["@ASIGNACION_FAMILIAR"]
    assert lst["base"]["afp"] is True and lst["vigente"]["afp"] is False and lst["origen"] == "EMPRESA"
    _recalc(cli, clave, B, pids[7])
    assert _tot(cli, clave, B, pids[7])["@FONDO_PENSIONES"] == D("311.30")                           # julio sigue como antes


def test_suspensiones_con_periodo_de_aplicacion(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    items = cli.get(V(B, "SUSPENSION"), headers=H(clave)).json()["items"]
    base = [i for i in items if i["base"]["tipo"] == "IMPERFECTA"][0]
    nuevo = "PERFECTA"
    assert _reg(cli, clave, B, "SUSPENSION", clave=base["clave"], valor={"tipo": nuevo}, aplica_desde="2026-09").status_code == 201
    en = lambda pid: {i["clave"]: i for i in cli.get(f"{V(B, 'SUSPENSION')}?periodo_id={pid}", headers=H(clave)).json()["items"]}[base["clave"]]
    assert en(pids[8])["vigente"]["tipo"] == "IMPERFECTA" and en(pids[9])["vigente"]["tipo"] == nuevo


def test_catalogos_con_vigencia_y_verificacion_de_colaboradores(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    ver = lambda pid: cli.get(f"{B}/config/catalogos/verificar?periodo_id={pid}", headers=H(clave)).json()["problemas"]
    assert ver(pids[9]) == []                                                                         # sin catálogo registrado no se revisa nada
    assert _reg(cli, clave, B, "CENTRO_COSTO", clave="000001101", valor={"nombre": "ADMINISTRATIVO"}, aplica_desde="2026-01").status_code == 201
    pr = ver(pids[9])
    assert [(p["colaborador"].split(",")[0], p["motivo"]) for p in pr] == [("ROJAS DIAZ", "sin asignar")]    # Ana coincide con «000001101 ADMINISTRATIVO»
    # el centro de costo se da de baja desde septiembre
    assert _reg(cli, clave, B, "CENTRO_COSTO", clave="000001101", valor={"nombre": "ADMINISTRATIVO", "activo": False}, aplica_desde="2026-09").status_code == 201
    assert {(p["colaborador"].split(",")[0], p["motivo"]) for p in ver(pids[9])} == {("QUISPE ROJAS", "no vigente en el periodo"), ("ROJAS DIAZ", "sin asignar")}
    assert [p for p in ver(pids[7]) if p["colaborador"].startswith("QUISPE")] == []                    # en julio todavía estaba vigente
    # puestos y áreas con un código que no coincide
    assert _reg(cli, clave, B, "AREA", clave="LOG", valor={"nombre": "LOGISTICA"}, aplica_desde="2026-01").status_code == 201
    assert any(p["familia"] == "AREA" and p["motivo"] == "no existe en el catálogo" for p in ver(pids[9]))
    assert _reg(cli, clave, B, "PUESTO", clave="X", valor={"nombre": ""}, aplica_desde="2026-01").status_code == 422
    lst = cli.get(V(B, "CENTRO_COSTO"), headers=H(clave)).json()
    assert len(lst["historial"]) == 2 and lst["items"][0]["vigente"]["activo"] is False


def test_no_se_altera_un_mes_cerrado_en_la_configuracion(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    assert cli.post(f"{B}/planillas/{pids[8]}/cerrar", headers=H(clave)).status_code == 200
    reg = lambda m: _reg(cli, clave, B, "PARAMETRO", clave="UIT", valor={"valor": "5600"}, aplica_desde=m).status_code
    assert reg("2026-08") == 409 and reg("2026-02") == 409
    ok = _reg(cli, clave, B, "PARAMETRO", clave="UIT", valor={"valor": "5600"}, aplica_desde="2026-09")
    assert ok.status_code == 201
    assert cli.post(f"{B}/planillas/{pids[9]}/cerrar", headers=H(clave)).status_code == 200
    assert cli.delete(f"{V(B, 'PARAMETRO')}/{ok.json()['id']}", headers=H(clave)).status_code == 409
    assert cli.get(V(B, "PARAMETRO"), headers=H(clave)).json()["historial"][0]["editable"] is False
