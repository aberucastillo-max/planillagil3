"""Post Planilla: códigos PLAME y plan de cuentas con periodo de aplicación; asiento contable."""
import io
import zipfile
from decimal import Decimal as D

from tests.test_liquidacion import API, H, _escenario

P = lambda B: f"{B}/config/plame"
C = lambda B: f"{B}/config/cuentas"


def _rem(cli, clave, B, pid):
    z = cli.get(f"{B}/planillas/{pid}/exportar-plame-zip", headers=H(clave))
    assert z.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    return zf.read([n for n in zf.namelist() if n.endswith(".rem")][0]).decode("latin-1")


def test_codigo_plame_con_periodo_de_aplicacion_y_uso_en_el_archivo(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    r = cli.get(P(B), headers=H(clave)).json()
    fila = {c["codigo"]: c for c in r["conceptos"]}["@HABER_BASICO"]
    base = fila["base"]
    assert base and fila["origen"] == "DICCIONARIO" and r["historial"] == []
    rem_antes = _rem(cli, clave, B, pids[9])
    assert f"|{base}|" in rem_antes and "|0999|" not in rem_antes
    # se registra un código propio desde agosto de 2026
    ok = cli.post(P(B), headers=H(clave), json={"concepto_codigo": "@HABER_BASICO", "codigo_plame": "0999", "aplica_desde": "2026-08", "nota": "prueba"})
    assert ok.status_code == 201 and ok.json()["aplica_desde"] == "2026-08" and ok.json()["codigo_plame"] == "0999"
    en = lambda pid: {c["codigo"]: c for c in cli.get(f"{P(B)}?periodo_id={pid}", headers=H(clave)).json()["conceptos"]}["@HABER_BASICO"]
    assert en(pids[7])["vigente"] == base and en(pids[7])["origen"] == "DICCIONARIO"             # antes de agosto rige el del Diccionario
    assert en(pids[9])["vigente"] == "0999" and en(pids[9])["origen"] == "EMPRESA" and en(pids[9])["desde"] == "2026-08"
    # el archivo PLAME de septiembre usa el código nuevo; el de julio, el anterior
    rem_sep = _rem(cli, clave, B, pids[9])
    assert "|0999|" in rem_sep and f"|{base}|" not in rem_sep
    assert f"|{base}|" in _rem(cli, clave, B, pids[7]) and "|0999|" not in _rem(cli, clave, B, pids[7])
    # validaciones
    mal = lambda **k: cli.post(P(B), headers=H(clave), json={"concepto_codigo": "@HABER_BASICO", "codigo_plame": "0999", "aplica_desde": "2026-09", **k}).status_code
    assert mal(codigo_plame="99") == 422 and mal(aplica_desde="2026-13") == 422 and mal(concepto_codigo="@NO_EXISTE") == 422
    assert cli.post(P(B), headers=H(clave), json={"concepto_codigo": "@HABER_BASICO", "codigo_plame": "0998", "aplica_desde": "2026-08"}).status_code == 409   # mismo mes
    # historial y eliminación
    h = cli.get(P(B), headers=H(clave)).json()["historial"]
    assert len(h) == 1 and h[0]["editable"] is True
    assert cli.delete(f"{P(B)}/{h[0]['id']}", headers=H(clave)).status_code == 200
    assert cli.get(P(B), headers=H(clave)).json()["historial"] == []
    assert f"|{base}|" in _rem(cli, clave, B, pids[9])


def test_no_se_altera_un_mes_cerrado(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    assert cli.post(f"{B}/planillas/{pids[8]}/cerrar", headers=H(clave)).status_code == 200
    cuerpo = lambda m: {"concepto_codigo": "@HABER_BASICO", "codigo_plame": "0999", "aplica_desde": m}
    assert cli.post(P(B), headers=H(clave), json=cuerpo("2026-08")).status_code == 409          # el mes cerrado
    assert cli.post(P(B), headers=H(clave), json=cuerpo("2026-03")).status_code == 409          # y los anteriores
    ok = cli.post(P(B), headers=H(clave), json=cuerpo("2026-09"))
    assert ok.status_code == 201
    assert cli.post(f"{B}/planillas/{pids[9]}/cerrar", headers=H(clave)).status_code == 200       # ahora septiembre se cierra con ese código
    assert cli.get(P(B), headers=H(clave)).json()["historial"][0]["editable"] is False
    assert cli.delete(f"{P(B)}/{ok.json()['id']}", headers=H(clave)).status_code == 409


def _config_completa(cli, clave, B, pid):
    res = cli.get(f"{B}/planillas/{pid}/resumen-conceptos", headers=H(clave)).json()["conceptos"]
    for c in res:
        cod, cat = c["codigo"], c["categoria"]
        cuerpo = {"concepto_codigo": cod, "aplica_desde": "2026-01"}
        if cat == "INGRESO":
            cuerpo["cuenta_debe"] = "621101"
        elif cat == "DESCUENTO":
            cuerpo["cuenta_haber"] = "401101"
        elif cat == "APORTE":
            cuerpo.update({"cuenta_debe": "627101", "cuenta_haber": "403101"})
        else:
            continue
        assert cli.post(C(B), headers=H(clave), json=cuerpo).status_code == 201, cod
    assert cli.post(C(B), headers=H(clave), json={"concepto_codigo": "@NETO_A_PAGAR", "cuenta_haber": "411101", "aplica_desde": "2026-01"}).status_code == 201


def test_asiento_con_plan_de_cuentas_cuadra_y_avisa_lo_que_falta(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    sin = cli.get(f"{B}/planillas/{pids[9]}/asiento", headers=H(clave)).json()
    assert sin["lineas"] == [] and sin["sin_cuenta"] and sin["definitivo"] is False                # sin plan de cuentas: todo falta
    # solo los ingresos tienen cuenta: no cuadra y avisa lo que falta
    ing = [c for c in cli.get(f"{B}/planillas/{pids[9]}/resumen-conceptos", headers=H(clave)).json()["conceptos"] if c["categoria"] == "INGRESO"]
    for c in ing:
        assert cli.post(C(B), headers=H(clave), json={"concepto_codigo": c["codigo"], "cuenta_debe": "621101", "aplica_desde": "2026-01"}).status_code == 201
    parcial = cli.get(f"{B}/planillas/{pids[9]}/asiento", headers=H(clave)).json()
    assert D(str(parcial["descuadre"])) > 0 and {f["categoria"] for f in parcial["sin_cuenta"]} >= {"DESCUENTO", "NETO"}
    # plan completo: el asiento cuadra
    for c in cli.get(C(B), headers=H(clave)).json()["historial"]:
        cli.delete(f"{C(B)}/{c['id']}", headers=H(clave))
    _config_completa(cli, clave, B, pids[9])
    a = cli.get(f"{B}/planillas/{pids[9]}/asiento", headers=H(clave)).json()
    assert a["sin_cuenta"] == [] and D(str(a["descuadre"])) == D("0.00") and D(str(a["total_debe"])) > 0
    assert D(str(a["total_debe"])) == D(str(a["total_haber"]))
    cuentas = {f["cuenta"]: f for f in a["lineas"]}
    assert {"621101", "401101", "411101"} <= set(cuentas) and D(str(cuentas["411101"]["haber"])) > 0 and D(str(cuentas["621101"]["haber"])) == 0
    # un periodo cerrado queda como asiento definitivo
    assert cli.post(f"{B}/planillas/{pids[9]}/cerrar", headers=H(clave)).status_code == 200
    assert cli.get(f"{B}/planillas/{pids[9]}/asiento", headers=H(clave)).json()["definitivo"] is True


def test_la_quincena_no_genera_asiento_y_el_neto_lleva_su_cuenta(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    assert cli.patch(B, headers=H(clave), json={"usa_quincena": True, "quincena_modo": "PORCENTAJE", "quincena_valor": 50}).status_code == 200
    q = cli.post(f"{B}/periodos", headers=H(clave), json={"anio": 2026, "mes": 10, "fase": "Quincena"}).json()["id"]
    assert cli.get(f"{B}/planillas/{q}/asiento", headers=H(clave)).status_code == 422
    r = cli.get(C(B), headers=H(clave)).json()
    assert [c for c in r["conceptos"] if c["codigo"] == "@NETO_A_PAGAR"] and not [c for c in r["conceptos"] if c["categoria"] == "PARAMETRO"]
    assert cli.post(C(B), headers=H(clave), json={"concepto_codigo": "@HABER_BASICO", "aplica_desde": "2026-01"}).status_code == 422     # sin ninguna cuenta
    assert cli.post(C(B), headers=H(clave), json={"concepto_codigo": "@HABER_BASICO", "cuenta_debe": "62-1", "aplica_desde": "2026-01"}).status_code == 422
