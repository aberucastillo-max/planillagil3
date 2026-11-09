"""Pruebas de la liquidación por cese con el escenario del documento de propuesta."""
import io
import zipfile
from decimal import Decimal as D

H = lambda k: {"X-API-Key": k}
API = "/api/v1/empresas"


def _escenario(cli, nueva):
    clave = nueva("Estudio", max_empresas=2)
    e = cli.post(API, json={"ruc": "20111111111", "razon_social": "Cliente Liq SAC"}, headers=H(clave)).json()["id"]
    B = f"{API}/{e}"
    ana = cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": "Ana", "apellidos": "Quispe Rojas", "documento_identidad": "45678912", "fecha_ingreso": "2025-03-01",
        "sueldo_basico": 3000, "sistema_pensionario": "AFP", "afp_nombre": "PRIMA", "cuspp": "123456ABCDE1",
        "asignacion_familiar": True, "numero_cuenta": "194-1"}).json()["id"]
    luis = cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": "Luis", "apellidos": "Rojas Diaz", "documento_identidad": "40123456", "fecha_ingreso": "2026-02-01",
        "sueldo_basico": 2000, "sistema_pensionario": "ONP"}).json()["id"]
    pids = {m: cli.post(f"{B}/periodos", json={"anio": 2026, "mes": m}, headers=H(clave)).json()["id"] for m in range(1, 11)}
    cli.post(f"{B}/empleados/{ana}/vacaciones", headers=H(clave), json={"fecha_inicio": "2026-01-05", "fecha_fin": "2026-01-19"})
    cli.put(f"{B}/periodos/{pids[7]}/novedades", headers=H(clave),
            json=[{"documento": "45678912", "codigo": "@GRATIFICACION", "valor": "3113"}])
    for m in range(1, 10):
        assert cli.post(f"{B}/planillas/{pids[m]}/recalcular", headers=H(clave)).status_code == 200
    assert cli.post(f"{B}/empleados/{ana}/baja", headers=H(clave), json={"fecha_cese": "2026-10-20", "motivo": "01"}).status_code == 200
    # motivo 02 exige la clase de despido
    assert cli.post(f"{B}/empleados/{luis}/baja", headers=H(clave), json={"fecha_cese": "2026-10-31", "motivo": "02"}).status_code == 422
    assert cli.post(f"{B}/empleados/{luis}/baja", headers=H(clave),
                    json={"fecha_cese": "2026-10-31", "motivo": "02", "clase_despido": "ARBITRARIO"}).status_code == 200
    assert cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave)).status_code == 200
    return clave, B, ana, luis, pids


def _conceptos(v):
    return {c["codigo"]: D(str(c["monto"])) for c in v["conceptos"]}


def test_lista_de_cesados_y_calculo_con_los_numeros_del_documento(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    lista = cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(clave)).json()
    assert lista["resumen"] == {"sin_iniciar": 2, "abiertas": 0, "cerradas": 0}
    assert {f["documento"] for f in lista["filas"]} == {"45678912", "40123456"}
    assert all(f["estado"] == "SIN_INICIAR" and f["origen"] == "MES" for f in lista["filas"])

    r = cli.post(f"{B}/liquidaciones", headers=H(clave),
                 json={"periodo_id": pids[10], "empleado_ids": [ana], "cts_anterior_pagada": True}).json()
    assert r["errores"] == [] and r["procesadas"][0]["estado"] == "ABIERTA"
    v = cli.get(f"{B}/liquidaciones/{r['procesadas'][0]['liquidacion_id']}", headers=H(clave)).json()
    c = _conceptos(v)
    assert c["@GRATIFICACION"] == D("1556.50") and c["@BONIF_EXTRA_29351"] == D("140.09")
    assert c["@CTS_TRUNCA"] == D("1715.03")                              # 1,513.26 + 201.77
    assert c["@VACACIONES_VENCIDAS"] == D("1556.50") and c["@VACACIONES_TRUNCAS"] == D("1988.86")
    assert D(str(v["total_ingresos"])) == D("6956.98")
    # aportes de pensión (AFP Prima) sobre las vacaciones: 10 %, 1.60 % y prima 1.37 % (sin tope)
    assert c["@FONDO_PENSIONES"] == D("354.54") and c["@COMISION_VARIABLE"] == D("56.73") and c["@SEGURO_INVALIDEZ"] == D("48.57")
    assert D(str(v["neto_a_pagar"])) == D(str(v["total_ingresos"])) - D(str(v["total_descuentos"]))
    assert "quinta" in v["snapshot"] and v["snapshot"]["vacaciones"]["vencidas"].startswith("15")
    assert v["puede_cerrar"]                                             # CTS respondida => se puede cerrar
    assert v["plazo_pago"] == "2026-10-22"                               # cese + 48 horas


def test_cts_anterior_sin_responder_bloquea_el_cierre_y_no_pagada_la_suma(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    lid = cli.post(f"{B}/liquidaciones", headers=H(clave), json={"periodo_id": pids[10], "empleado_ids": [ana]}).json()["procesadas"][0]["liquidacion_id"]
    v = cli.get(f"{B}/liquidaciones/{lid}", headers=H(clave)).json()
    assert v["cts_anterior_aplica"] and v["cts_anterior_pagada"] is None and not v["puede_cerrar"]
    assert cli.post(f"{B}/liquidaciones/{lid}/cerrar", headers=H(clave)).status_code == 409
    v = cli.patch(f"{B}/liquidaciones/{lid}", headers=H(clave), json={"cts_anterior_pagada": False}).json()
    cts = [c for c in v["conceptos"] if c["codigo"] == "@CTS_TRUNCA"]
    assert len(cts) == 2 and sorted(D(str(c["monto"])) for c in cts) == [D("1556.50"), D("1715.03")]
    assert cli.patch(f"{B}/liquidaciones/{lid}", headers=H(clave), json={"cts_anterior_pagada": True}).json()["puede_cerrar"]


def test_indemnizacion_por_despido_arbitrario(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    lid = cli.post(f"{B}/liquidaciones", headers=H(clave),
                   json={"periodo_id": pids[10], "empleado_ids": [luis], "cts_anterior_pagada": True}).json()["procesadas"][0]["liquidacion_id"]
    c = _conceptos(cli.get(f"{B}/liquidaciones/{lid}", headers=H(clave)).json())
    assert c["@INDEMNIZACION_DESPIDO"] == D("2250.00")                   # 1.5 × 2,000 × 9/12
    assert c["@GRATIFICACION"] == D("1333.33") and c["@BONIF_EXTRA_29351"] == D("120.00")
    assert c["@VACACIONES_TRUNCAS"] == D("1500.00") and c["@CTS_TRUNCA"] == D("1138.89")   # RC 2,000 + 1/6 de la gratificación automática de julio (277.78)
    assert "@FONDO_PENSIONES" not in c and c["@SNP_ONP"] == D("195.00")   # ONP 13 % de 1,500


def test_cierre_bloquea_la_boleta_del_colaborador_y_la_reapertura_la_libera(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    lid = cli.post(f"{B}/liquidaciones", headers=H(clave),
                   json={"periodo_id": pids[10], "empleado_ids": [ana], "cts_anterior_pagada": True}).json()["procesadas"][0]["liquidacion_id"]
    antes = [f for f in cli.get(f"{B}/planillas/{pids[10]}/resultados", headers=H(clave)).json()["filas"] if f["documento"] == "45678912"][0]
    assert cli.post(f"{B}/liquidaciones/{lid}/cerrar", headers=H(clave)).json()["estado"] == "CERRADA"
    # Recalcular omite a Ana y conserva su resultado
    rec = cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave)).json()
    assert rec["bloqueados_por_liquidacion"] == 1
    despues = [f for f in cli.get(f"{B}/planillas/{pids[10]}/resultados", headers=H(clave)).json()["filas"] if f["documento"] == "45678912"][0]
    assert despues["neto"] == antes["neto"] and despues["liquidacion_estado"] == "CERRADA"
    assert D(str(despues["total_consolidado"])) == D(str(despues["neto"])) + D(str(despues["liquidacion_neto"]))
    # no se puede tocar sus datos
    assert cli.post(f"{B}/empleados/{ana}/sueldos", headers=H(clave), json={"monto": 3500, "fecha_vigencia": "2026-10-01"}).status_code == 403
    assert cli.post(f"{B}/empleados/{ana}/ausentismos", headers=H(clave),
                    json={"codigo_plame": "06", "fecha_inicio": "2026-10-02", "fecha_fin": "2026-10-02"}).status_code == 403
    assert cli.post(f"{B}/empleados/{ana}/reactivar-por-error", headers=H(clave)).status_code == 403
    nov = cli.put(f"{B}/periodos/{pids[10]}/novedades", headers=H(clave),
                  json=[{"documento": "45678912", "codigo": "@COMISIONES", "valor": "10"}])
    assert nov.status_code == 422 and "CERRADA" in nov.json()["errores"][0]["error"]
    assert cli.post(f"{B}/liquidaciones/{lid}/reabrir", headers=H(clave), json={"motivo": "ok"}).status_code == 422
    assert cli.post(f"{B}/liquidaciones/{lid}/reabrir", headers=H(clave), json={"motivo": "Corrección de vacaciones"}).json()["estado"] == "ABIERTA"
    assert cli.post(f"{B}/empleados/{ana}/sueldos", headers=H(clave), json={"monto": 3500, "fecha_vigencia": "2026-10-01"}).status_code == 201


def test_exportables_consolidados_en_un_solo_archivo(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    la = cli.post(f"{B}/liquidaciones", headers=H(clave), json={"periodo_id": pids[10], "empleado_ids": [ana, luis],
                                                                  "cts_anterior_pagada": True}).json()
    ids = {p["empleado_id"]: p["liquidacion_id"] for p in la["procesadas"]}
    cli.post(f"{B}/liquidaciones/{ids[ana]}/cerrar", headers=H(clave))             # Luis queda ABIERTA
    afp = cli.get(f"{B}/planillas/{pids[10]}/exportar-afpnet?formato=csv", headers=H(clave))
    filas = [l.split(",") for l in afp.text.strip().splitlines()]
    assert len(filas) == 1 and filas[0][3] == "45678912"                            # una sola fila (solo Ana está en AFP)
    assert float(filas[0][11]) == 5620.69                                           # boleta 2,075.33 + vacaciones 3,545.36
    assert "liquidaciones-incluidas=1" in afp.headers["x-avisos"] and "liquidaciones-abiertas-no-incluidas=1" in afp.headers["x-avisos"]
    z = cli.get(f"{B}/planillas/{pids[10]}/exportar-plame-zip", headers=H(clave))
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    rem = zf.read([n for n in zf.namelist() if n.endswith("rem")][0]).decode("latin-1")
    lineas = [l for l in rem.splitlines() if l.startswith("01|45678912|")]
    codigos = {l.split("|")[2]: l.split("|")[3] for l in lineas}
    assert codigos["0118"] == "3545.36" and codigos["0406"] == "1556.50" and codigos["0312"] == "140.09"   # liquidación sumada
    assert codigos["0121"] == "2000.00" and codigos["0201"] == "75.33"                                    # boleta
    assert len(lineas) == len(codigos)                                                                    # un solo renglón por código
    assert "@CTS_TRUNCA" in z.headers["x-avisos"]                                                         # sin código PLAME todavía
    assert not any("40123456" in l for l in rem.splitlines() if l.split("|")[2] in ("0406", "0312", "0118"))   # Luis (abierta) no entra


def test_pdf_borrador_y_definitivo_y_periodo_cerrado(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    lid = cli.post(f"{B}/liquidaciones", headers=H(clave),
                   json={"periodo_id": pids[10], "empleado_ids": [ana], "cts_anterior_pagada": True}).json()["procesadas"][0]["liquidacion_id"]
    borrador = cli.get(f"{B}/liquidaciones/{lid}/pdf", headers=H(clave))
    assert borrador.status_code == 200 and borrador.content.startswith(b"%PDF") and "BORRADOR" in borrador.headers["content-disposition"]
    cli.post(f"{B}/liquidaciones/{lid}/cerrar", headers=H(clave))
    definitivo = cli.get(f"{B}/liquidaciones/{lid}/pdf?firmas=true&formulas=false", headers=H(clave))
    assert definitivo.content.startswith(b"%PDF") and "BORRADOR" not in definitivo.headers["content-disposition"]
    # con el periodo del cese CERRADO no se procesan liquidaciones nuevas (decisión pendiente del contador)
    assert cli.post(f"{B}/planillas/{pids[10]}/cerrar", headers=H(clave)).status_code == 200
    r = cli.post(f"{B}/liquidaciones", headers=H(clave), json={"periodo_id": pids[10], "empleado_ids": [luis]}).json()
    assert r["procesadas"] == [] and "CERRADO" in r["errores"][0]["error"]


def test_pendientes_de_meses_anteriores_y_aislamiento(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    cli.post(f"{B}/liquidaciones", headers=H(clave), json={"periodo_id": pids[10], "empleado_ids": [luis], "cts_anterior_pagada": True})
    nov = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 11}, headers=H(clave)).json()["id"]
    lista = cli.get(f"{B}/liquidaciones?periodo_id={nov}", headers=H(clave)).json()
    assert [(f["documento"], f["estado"], f["origen"]) for f in lista["filas"] if f["documento"] == "40123456"] == [("40123456", "ABIERTA", "PENDIENTE_ANTERIOR")]
    otra = nueva("Otra cuenta")
    assert cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(otra)).status_code == 404
    # el cese debe caer en el periodo elegido
    r = cli.post(f"{B}/liquidaciones", headers=H(clave), json={"periodo_id": pids[9], "empleado_ids": [ana]}).json()
    assert "no cae" in r["errores"][0]["error"]
