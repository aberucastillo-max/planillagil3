"""Regla de la 5ta del cesado y opciones de la liquidación en tiempo real (vacaciones pendientes, simulación por concepto)."""
from decimal import Decimal as D

from tests.test_liquidacion import API, H, _cerrar, _conceptos, _escenario, _vista

OPC = lambda **k: "".join(f"&{a}={b}" for a, b in k.items())


def _vista_opc(cli, clave, B, pid, emp, **k):
    r = cli.get(f"{B}/liquidaciones/vista?periodo_id={pid}&empleado_id={emp}{OPC(**k)}", headers=H(clave))
    assert r.status_code == 200, r.text
    return r.json()


def test_regla_5ta_del_cesado_todo_se_aplica_en_la_liquidacion(entorno):
    cli, nueva = entorno
    clave = nueva("Estudio", max_empresas=2)
    e = cli.post(API, json={"ruc": "20666666666", "razon_social": "Quinta SAC"}, headers=H(clave)).json()["id"]
    B = f"{API}/{e}"
    mk = lambda dni, n: cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": n, "apellidos": n + " Prueba", "documento_identidad": dni, "fecha_ingreso": "2026-01-01", "sueldo_basico": 12000,
        "sistema_pensionario": "ONP", "numero_cuenta": "1"}).json()["id"]
    cesa, sigue = mk("45678912", "Cesa"), mk("40123456", "Sigue")
    pids = {m: cli.post(f"{B}/periodos", json={"anio": 2026, "mes": m}, headers=H(clave)).json()["id"] for m in range(1, 11)}
    for m in range(1, 10):
        cli.post(f"{B}/planillas/{pids[m]}/recalcular", headers=H(clave))
    cli.post(f"{B}/empleados/{cesa}/baja", headers=H(clave), json={"fecha_cese": "2026-10-20", "motivo": "01"})
    r = cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave)).json()
    det = lambda pid, emp: {c["codigo"]: D(str(c["monto"])) for c in cli.get(f"{B}/planillas/{pid}/resultados/{emp}/detalle", headers=H(clave)).json()["conceptos"]}
    # la boleta del mes del cese NO retiene 5ta; la del que sigue trabajando sí
    assert "@IMP_RENTA_5TA" not in det(pids[10], cesa) and det(pids[10], sigue)["@IMP_RENTA_5TA"] > 0
    assert any("CESA" in a and "se aplica en su liquidación" in a for a in r["alertas"])
    # la liquidación aplica el resto: lo retenido en los meses anteriores + el ajuste = impuesto anual de la renta real
    v = _vista_opc(cli, clave, B, pids[10], cesa, cts_anterior_pagada="true")
    ajuste = _conceptos(v)["@IMP_RENTA_5TA"]
    retenido = sum((det(pids[m], cesa).get("@IMP_RENTA_5TA", D(0)) for m in range(1, 11)), D(0))
    assert ajuste > 0 and retenido + ajuste == D(v["snapshot"]["quinta"]["impuesto_anual"])
    assert D(v["snapshot"]["quinta"]["retenido"]) == retenido
    # y en el neto de la liquidación se ve ese descuento
    assert D(str(v["total_descuentos"])) >= ajuste


def test_vacaciones_pendientes_formula_cero_dias_y_monto(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    base = _vista_opc(cli, clave, B, pids[10], ana, cts_anterior_pagada="true")
    assert _conceptos(base)["@VACACIONES_VENCIDAS"] == D("1556.50") and base["snapshot"]["opciones"]["vacaciones_pendientes"]["formula_dias"] == "15.0000"
    # 0: no se pagan, y se avisa lo que serían por fórmula
    cero = _vista_opc(cli, clave, B, pids[10], ana, cts_anterior_pagada="true", vac_modo="CERO")
    assert "@VACACIONES_VENCIDAS" not in _conceptos(cero) and any("deshabilitadas" in a for a in cero["avisos"])
    assert D(str(cero["total_ingresos"])) == D(str(base["total_ingresos"])) - D("1556.50")
    # escribir días: 3,113 / 30 × 10
    dias = _vista_opc(cli, clave, B, pids[10], ana, cts_anterior_pagada="true", vac_modo="DIAS", vac_valor=10)
    assert _conceptos(dias)["@VACACIONES_VENCIDAS"] == D("1037.67") and "ESCRITOS" in [c for c in dias["conceptos"] if c["codigo"] == "@VACACIONES_VENCIDAS"][0]["formula"]
    assert dias["snapshot"]["vacaciones"]["vencidas"] == "10.0000"
    # escribir el monto
    monto = _vista_opc(cli, clave, B, pids[10], ana, cts_anterior_pagada="true", vac_modo="MONTO", vac_valor=2000)
    assert _conceptos(monto)["@VACACIONES_VENCIDAS"] == D("2000.00") and "ESCRITO" in [c for c in monto["conceptos"] if c["codigo"] == "@VACACIONES_VENCIDAS"][0]["formula"]
    # la base de AFP sigue las vacaciones (afectas): cambia el aporte
    assert _conceptos(monto)["@FONDO_PENSIONES"] > _conceptos(base)["@FONDO_PENSIONES"] > _conceptos(cero)["@FONDO_PENSIONES"]
    # validaciones
    assert cli.get(f"{B}/liquidaciones/vista?periodo_id={pids[10]}&empleado_id={ana}&vac_modo=DIAS", headers=H(clave)).status_code == 422
    assert cli.get(f"{B}/liquidaciones/vista?periodo_id={pids[10]}&empleado_id={ana}&vac_modo=MONTO&vac_valor=-5", headers=H(clave)).status_code == 422
    assert cli.get(f"{B}/liquidaciones/vista?periodo_id={pids[10]}&empleado_id={ana}&vac_modo=OTRO", headers=H(clave)).status_code == 422
    # nada se guardó al probar opciones
    assert cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(clave)).json()["resumen"]["cerradas"] == 0
    # al CERRAR se guarda la decisión de vacaciones pendientes
    cerr = cli.post(f"{B}/liquidaciones/cerrar", headers=H(clave), json={"periodo_id": pids[10], "empleado_id": ana, "cts_anterior_pagada": True, "vac_modo": "MONTO", "vac_valor": 2000})
    assert cerr.status_code == 200 and _conceptos(cerr.json())["@VACACIONES_VENCIDAS"] == D("2000.00")
    ver = _vista(cli, clave, B, pids[10], ana)
    assert ver["estado"] == "CERRADA" and ver["snapshot"]["opciones"]["vacaciones_pendientes"]["modo"] == "MONTO" and any("escrito manualmente" in a for a in ver["avisos"])
    afp = cli.get(f"{B}/planillas/{pids[10]}/exportar-afpnet?formato=csv", headers=H(clave))
    assert float([l.split(",") for l in afp.text.strip().splitlines()][0][11]) == float(D("2075.33") + D("1988.86") + D("2000.00"))     # boleta + truncas + lo escrito


def test_simulacion_deshabilitar_conceptos_no_se_puede_cerrar(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    base = _vista_opc(cli, clave, B, pids[10], ana, cts_anterior_pagada="true")
    assert not base["simulacion"] and all(c["habilitado"] for c in base["conceptos"])
    claves = {c["clave"] for c in base["conceptos"]}
    assert {"@CTS_TRUNCA", "@VACACIONES_TRUNCAS", "@GRATIFICACION"} <= claves
    sim = _vista_opc(cli, clave, B, pids[10], ana, cts_anterior_pagada="true", excluir="@VACACIONES_TRUNCAS,@CTS_TRUNCA")
    assert sim["simulacion"] and not sim["puede_cerrar"] and any("SIMULACIÓN" in f for f in sim["faltantes_para_cerrar"])
    por = {c["clave"]: c for c in sim["conceptos"]}
    assert por["@VACACIONES_TRUNCAS"]["habilitado"] is False and por["@CTS_TRUNCA"]["habilitado"] is False and por["@GRATIFICACION"]["habilitado"] is True
    quitado = D("1988.86") + D("1715.03")
    assert D(str(sim["total_ingresos"])) == D(str(base["total_ingresos"])) - quitado
    # las bases se recalculan sin lo deshabilitado: la AFP solo sobre las vacaciones vencidas
    assert _conceptos(sim)["@FONDO_PENSIONES"] == D("155.65") and _conceptos(base)["@FONDO_PENSIONES"] == D("354.54")
    # con la CTS del semestre anterior también hay dos filas con el mismo código: se distinguen por clave
    dos = _vista_opc(cli, clave, B, pids[10], ana, cts_anterior_pagada="false")
    assert [c["clave"] for c in dos["conceptos"] if c["codigo"] == "@CTS_TRUNCA"] == ["@CTS_TRUNCA", "@CTS_TRUNCA:2"]
    solo2 = _vista_opc(cli, clave, B, pids[10], ana, cts_anterior_pagada="false", excluir="@CTS_TRUNCA:2")
    assert {c["clave"]: c["habilitado"] for c in solo2["conceptos"] if c["codigo"] == "@CTS_TRUNCA"} == {"@CTS_TRUNCA": True, "@CTS_TRUNCA:2": False}
    # el PDF y el Excel de una simulación salen sin lo deshabilitado y marcados como tal; nada queda guardado
    q = f"periodo_id={pids[10]}&empleado_id={ana}&cts_anterior_pagada=true&excluir=@CTS_TRUNCA"
    assert cli.get(f"{B}/liquidaciones/pdf?{q}", headers=H(clave)).content.startswith(b"%PDF")
    assert cli.get(f"{B}/liquidaciones/excel?{q}", headers=H(clave)).content[:2] == b"PK"
    assert cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(clave)).json()["resumen"]["cerradas"] == 0
    # cerrar siempre lo hace con todos los conceptos habilitados
    cerr = _cerrar(cli, clave, B, pids[10], ana).json()
    assert {c["codigo"] for c in cerr["conceptos"]} >= {"@CTS_TRUNCA", "@VACACIONES_TRUNCAS"} and cerr["simulacion"] is False


def test_detalle_de_la_renta_de_5ta_de_donde_sale_la_renta_bruta(entorno):
    cli, nueva = entorno
    clave = nueva("Estudio", max_empresas=2)
    e = cli.post(API, json={"ruc": "20777777777", "razon_social": "Detalle 5ta SAC"}, headers=H(clave)).json()["id"]
    B = f"{API}/{e}"
    ana = cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": "Ana", "apellidos": "Alta Renta", "documento_identidad": "45678912", "fecha_ingreso": "2025-03-01", "sueldo_basico": 10658.55,
        "sistema_pensionario": "ONP", "numero_cuenta": "1"}).json()["id"]
    cli.post(f"{B}/empleados/{ana}/rentas-previas-5ta", headers=H(clave), json={"anio": 2026, "mes": 1, "ruc": "20512345678", "razon_social": "Otra SAC", "ingreso_afecto": 5000, "retencion": 100})
    pids = {m: cli.post(f"{B}/periodos", json={"anio": 2026, "mes": m}, headers=H(clave)).json()["id"] for m in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)}
    for m in range(1, 10):
        cli.post(f"{B}/planillas/{pids[m]}/recalcular", headers=H(clave))
    cli.post(f"{B}/empleados/{ana}/baja", headers=H(clave), json={"fecha_cese": "2026-10-20", "motivo": "01"})
    cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave))
    v = _vista_opc(cli, clave, B, pids[10], ana, cts_anterior_pagada="true")
    q = v["snapshot"]["quinta"]
    d = q["detalle"]
    # los tres orígenes de la renta bruta, mes por mes
    assert [b["mes"] for b in d["boletas"]] == list(range(1, 11)) and all(D(b["base"]) > 0 for b in d["boletas"])
    assert D(d["subtotales"]["boletas"]) == sum(D(b["base"]) for b in d["boletas"])
    assert d["rentas_previas"][0]["ruc"] == "20512345678" and D(d["subtotales"]["rentas_previas"]) == D("5000")
    assert {c["codigo"] for c in d["liquidacion"]} >= {"@GRATIFICACION", "@BONIF_EXTRA_29351", "@VACACIONES_TRUNCAS"}
    suma = D(d["subtotales"]["boletas"]) + D(d["subtotales"]["rentas_previas"]) + D(d["subtotales"]["liquidacion"])
    assert D(d["renta_bruta"]) == D(q["renta_bruta"]) == suma                                # la cifra «sobre renta» = A + B + C
    assert D(d["renta_neta"]) == D(d["renta_bruta"]) - D(d["deduccion"]) and D(d["deduccion"]) == D("7") * D(d["uit"])
    assert sum(D(t["impuesto"]) for t in d["tramos"]) == D(d["impuesto_anual"])               # el impuesto es la suma de los tramos
    assert sum(D(t["monto_en_tramo"]) for t in d["tramos"]) == D(d["renta_neta"])
    assert D(d["retenido_empresa"]) + D(d["retenido_otros"]) == D(q["retenido"]) and D(d["retenido_otros"]) == D("100")
    assert D(d["impuesto_anual"]) - D(d["retenido_empresa"]) - D(d["retenido_otros"]) == D(d["ajuste"])
    # la fórmula del renglón cita la renta bruta y remite al detalle
    f5 = [c["formula"] for c in v["conceptos"] if c["codigo"] in ("@IMP_RENTA_5TA", "@DEVOLUCION_5TA")][0]
    assert f"renta bruta {D(d['renta_bruta']):,.2f}" in f5 and "Ver detalle de la 5ta" in f5
    # PDF con detalle y Excel con hoja propia
    pdf = cli.get(f"{B}/liquidaciones/pdf?periodo_id={pids[10]}&empleado_id={ana}&cts_anterior_pagada=true&formulas=true", headers=H(clave))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(cli.get(f"{B}/liquidaciones/excel?periodo_id={pids[10]}&empleado_id={ana}&cts_anterior_pagada=true".replace("anterior_pagada", "anterior_pagada"), headers=H(clave)).content))
    assert "Detalle 5ta categoría" in wb.sheetnames
    # una boleta faltante se avisa: la renta bruta estaría incompleta
    sin = cli.post(f"{B}/empleados", headers=H(clave), json={"nombres": "Luis", "apellidos": "Incompleto", "documento_identidad": "40123456", "fecha_ingreso": "2025-01-01",
                                                              "sueldo_basico": 5000, "sistema_pensionario": "ONP", "numero_cuenta": "2"}).json()["id"]
    cli.post(f"{B}/empleados/{sin}/baja", headers=H(clave), json={"fecha_cese": "2026-10-31", "motivo": "01"})
    cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave))
    av = _vista_opc(cli, clave, B, pids[10], sin, cts_anterior_pagada="true")["avisos"]
    assert any("no hay boleta calculada de los meses 01, 02, 03, 04, 05, 06, 07, 08, 09" in a for a in av)


def test_la_quincena_es_un_adelanto_y_no_entra_en_ninguna_acumulacion(entorno):
    """La quincena (adelanto) no suma a la renta bruta de 5ta, ni a los promedios, ni a la liquidación: solo Fin de Mes cuenta."""
    cli, nueva = entorno
    clave = nueva("Estudio", max_empresas=2)
    e = cli.post(API, json={"ruc": "20888888888", "razon_social": "Fases SAC", "usa_quincena": True}, headers=H(clave)).json()["id"]
    B = f"{API}/{e}"
    mk = lambda dni, n: cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": n, "apellidos": n + " Prueba", "documento_identidad": dni, "fecha_ingreso": "2026-01-01", "sueldo_basico": 10000,
        "sistema_pensionario": "ONP", "numero_cuenta": "1"}).json()["id"]
    cesa, sigue = mk("45678912", "Cesa"), mk("40123456", "Sigue")
    pids = {m: cli.post(f"{B}/periodos", json={"anio": 2026, "mes": m}, headers=H(clave)).json()["id"] for m in range(1, 12)}
    for m in range(1, 10):
        cli.post(f"{B}/planillas/{pids[m]}/recalcular", headers=H(clave))
    cli.post(f"{B}/empleados/{cesa}/baja", headers=H(clave), json={"fecha_cese": "2026-10-31", "motivo": "01"})
    cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave))
    antes = _vista_opc(cli, clave, B, pids[10], cesa, cts_anterior_pagada="true")
    bruta_antes = D(antes["snapshot"]["quinta"]["renta_bruta"])
    cli.post(f"{B}/planillas/{pids[11]}/recalcular", headers=H(clave))
    ret_nov = lambda: D(str(next(c["monto"] for c in cli.get(f"{B}/planillas/{pids[11]}/resultados/{sigue}/detalle", headers=H(clave)).json()["conceptos"] if c["codigo"] == "@IMP_RENTA_5TA")))
    nov_antes = ret_nov()
    # se procesan las quincenas de setiembre y octubre (adelantos)
    q9 = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 9, "fase": "Quincena"}, headers=H(clave)).json()
    q10 = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 10, "fase": "Quincena"}, headers=H(clave)).json()
    assert "ADELANTO" in q10["aviso"]
    assert cli.post(f"{B}/planillas/{q10['id']}/recalcular", headers=H(clave)).status_code == 200
    cli.post(f"{B}/planillas/{q9['id']}/recalcular", headers=H(clave))
    v = _vista_opc(cli, clave, B, pids[10], cesa, cts_anterior_pagada="true")
    d = v["snapshot"]["quinta"]["detalle"]
    assert D(v["snapshot"]["quinta"]["renta_bruta"]) == bruta_antes                       # la quincena no suma a la renta bruta
    assert [b["mes"] for b in d["boletas"]] == list(range(1, 11)) and all(b["fase"] == "Fin de Mes" for b in d["boletas"])
    assert d["boletas_no_sumadas"] == []
    assert D(d["renta_bruta"]) == D(d["subtotales"]["boletas"]) + D(d["subtotales"]["rentas_previas"]) + D(d["subtotales"]["liquidacion"])
    # pedir la liquidación desde la fase Quincena exige la planilla de fin de mes (y usa la vigente si existe)
    via_q = _vista_opc(cli, clave, B, q10["id"], cesa, cts_anterior_pagada="true")
    assert D(str(via_q["neto_a_pagar"])) == D(str(v["neto_a_pagar"]))
    # y la retención mensual de 5ta de quien sigue trabajando tampoco cambia
    cli.post(f"{B}/planillas/{pids[11]}/recalcular", headers=H(clave))
    assert ret_nov() == nov_antes


def _afp_cese(cli, nueva, ruc, sueldo, cese):
    clave = nueva("Estudio", max_empresas=2)
    e = cli.post(API, json={"ruc": ruc, "razon_social": "Prima Tope SAC"}, headers=H(clave)).json()["id"]
    B = f"{API}/{e}"
    emp = cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": "Ana", "apellidos": "Prima Tope", "documento_identidad": "45678912", "fecha_ingreso": "2025-03-01", "sueldo_basico": sueldo,
        "sistema_pensionario": "AFP", "afp_nombre": "PRIMA", "cuspp": "123456ABCDE1", "numero_cuenta": "1"}).json()["id"]
    pids = {m: cli.post(f"{B}/periodos", json={"anio": 2026, "mes": m}, headers=H(clave)).json()["id"] for m in range(1, 11)}
    for m in range(1, 10):
        cli.post(f"{B}/planillas/{pids[m]}/recalcular", headers=H(clave))
    cli.post(f"{B}/empleados/{emp}/baja", headers=H(clave), json={"fecha_cese": cese, "motivo": "01"})
    # el Diccionario solo carga el primer periodo: el tope vigente desde julio lo actualiza el usuario en el periodo de octubre
    assert cli.put(f"{B}/periodos/{pids[10]}/parametros", headers=H(clave), json={"parametros": {"RMA_PRIMA": "12732.70"}}).status_code == 200
    return clave, B, emp, pids


def _prima_boleta(cli, clave, B, pid, emp):
    return D(str(next((c["monto"] for c in cli.get(f"{B}/planillas/{pid}/resultados/{emp}/detalle", headers=H(clave)).json()["conceptos"]
                       if c["codigo"] == "@SEGURO_INVALIDEZ"), 0)))


def test_prima_del_seguro_boleta_mas_liquidacion_no_supera_el_tope(entorno):
    cli, nueva = entorno
    # (1) la boleta del mes YA llegó al tope: la liquidación no cobra más prima
    clave, B, emp, pids = _afp_cese(cli, nueva, "20991111111", 15000, "2026-10-31")
    cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave))
    v = _vista_opc(cli, clave, B, pids[10], emp, cts_anterior_pagada="true")
    p = v["snapshot"]["pension"]["prima"]
    boleta = _prima_boleta(cli, clave, B, pids[10], emp)
    assert D(p["tope_rma"]) == D("12732.70") and boleta == D("174.44") == D(p["cobrado_boleta"]) == D(p["maxima_mes"])
    assert "@SEGURO_INVALIDEZ" not in _conceptos(v) and D(p["adicional"]) == 0          # nada adicional: ya está en el máximo
    # (2) la boleta quedó por debajo del tope y la suma lo supera: la liquidación solo completa hasta el máximo
    clave, B, emp, pids = _afp_cese(cli, nueva, "20992222222", 10000, "2026-10-20")
    cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave))
    v = _vista_opc(cli, clave, B, pids[10], emp, cts_anterior_pagada="true")
    p = v["snapshot"]["pension"]["prima"]
    boleta = _prima_boleta(cli, clave, B, pids[10], emp)
    liq = _conceptos(v)["@SEGURO_INVALIDEZ"]
    assert D(p["base_boleta"]) == D("6666.67") and boleta == D("91.33") == D(p["cobrado_boleta"])
    assert D(p["base_total"]) > D(p["tope_rma"]) and D(p["base_con_tope"]) == D(p["tope_rma"])
    maxima = (D("12732.70") * D("0.0137")).quantize(D("0.01"))
    assert boleta + liq == maxima == D(p["maxima_mes"])                                  # boleta + liquidación = prima máxima, ni un céntimo más
    assert liq == D(p["adicional"]) and liq < D(p["propia_liquidacion"])
    assert "ya cobrado en la boleta 91.33" in [c["formula"] for c in v["conceptos"] if c["codigo"] == "@SEGURO_INVALIDEZ"][0]
    # (3) el tope es el CONFIGURADO PARA EL PERIODO (Cálculo de Planilla), igual en la boleta y en la liquidación
    cli.put(f"{B}/periodos/{pids[10]}/parametros", headers=H(clave), json={"parametros": {"RMA_PRIMA": "8000"}})
    cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave))
    v = _vista_opc(cli, clave, B, pids[10], emp, cts_anterior_pagada="true")
    p = v["snapshot"]["pension"]["prima"]
    boleta = _prima_boleta(cli, clave, B, pids[10], emp)
    liq = _conceptos(v)["@SEGURO_INVALIDEZ"]
    assert D(p["tope_rma"]) == D("8000") and boleta == D("91.33")
    assert boleta + liq == D("109.60") == (D("8000") * D("0.0137")).quantize(D("0.01"))     # 8,000 × 1.37 %
    # (4) y al cerrar queda guardado exactamente eso
    cerr = _cerrar(cli, clave, B, pids[10], emp).json()
    assert _conceptos(cerr)["@SEGURO_INVALIDEZ"] == liq and cerr["snapshot"]["pension"]["prima"]["adicional"] == p["adicional"]


def test_aviso_si_la_boleta_se_calculo_con_otro_tope(entorno):
    cli, nueva = entorno
    clave, B, emp, pids = _afp_cese(cli, nueva, "20993333333", 10000, "2026-10-20")
    cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave))
    ok = _vista_opc(cli, clave, B, pids[10], emp, cts_anterior_pagada="true")
    assert not any("no coincide con la que da el tope actual" in a for a in ok["avisos"])
    cli.put(f"{B}/periodos/{pids[10]}/parametros", headers=H(clave), json={"parametros": {"RMA_PRIMA": "5000"}})      # se cambia el tope SIN recalcular la boleta
    v = _vista_opc(cli, clave, B, pids[10], emp, cts_anterior_pagada="true")
    assert any("no coincide con la que da el tope actual" in a and "Procesar Planilla" in a for a in v["avisos"])
    p = v["snapshot"]["pension"]["prima"]
    # aun así la suma nunca supera el máximo del tope vigente
    assert D(p["cobrado_boleta"]) + D(p["adicional"]) <= D(p["maxima_mes"]) or D(p["adicional"]) == 0
