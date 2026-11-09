"""Liquidación por cese (etapa 4): se calcula al «Ver»/PDF/Excel SIN guardar; solo al CERRAR se guarda y viaja al AFPnet, al PLAME y a los resúmenes."""
import io
import zipfile
from decimal import Decimal as D

H = lambda k: {"X-API-Key": k}
API = "/api/v1/empresas"


def _escenario(cli, nueva):
    clave = nueva("Estudio", max_empresas=2)
    e = cli.post(API, json={"ruc": "20111111111", "razon_social": "Cliente Liq SAC", "direccion_fiscal": "AV. PRUEBA 123, LIMA",
                            "representante_legal": "GERENTE DE PRUEBA", "cargo_representante": "GERENTE GENERAL"}, headers=H(clave)).json()["id"]
    B = f"{API}/{e}"
    ana = cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": "Ana", "apellidos": "Quispe Rojas", "documento_identidad": "45678912", "fecha_ingreso": "2025-03-01",
        "sueldo_basico": 3000, "sistema_pensionario": "AFP", "afp_nombre": "PRIMA", "cuspp": "123456ABCDE1",
        "asignacion_familiar": True, "numero_cuenta": "194-1", "sede": "MAGDALENA", "area": "ADMINISTRACION", "centro_costo": "000001101 ADMINISTRATIVO",
        "categoria": "EMPLEADOS", "calificacion": "CONFIANZA - NO FISCAL", "puesto": "AUDITORA"}).json()["id"]
    luis = cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": "Luis", "apellidos": "Rojas Diaz", "documento_identidad": "40123456", "fecha_ingreso": "2026-02-01",
        "sueldo_basico": 2000, "sistema_pensionario": "ONP", "numero_cuenta": "5"}).json()["id"]
    pids = {m: cli.post(f"{B}/periodos", json={"anio": 2026, "mes": m}, headers=H(clave)).json()["id"] for m in range(1, 11)}
    cli.post(f"{B}/empleados/{ana}/vacaciones", headers=H(clave), json={"fecha_inicio": "2026-01-05", "fecha_fin": "2026-01-19"})
    cli.put(f"{B}/periodos/{pids[7]}/novedades", headers=H(clave),
            json=[{"documento": "45678912", "codigo": "@GRATIFICACION", "valor": "3113"}])
    for m in range(1, 10):
        assert cli.post(f"{B}/planillas/{pids[m]}/recalcular", headers=H(clave)).status_code == 200
    assert cli.post(f"{B}/empleados/{ana}/baja", headers=H(clave), json={"fecha_cese": "2026-10-20", "motivo": "01"}).status_code == 200
    assert cli.post(f"{B}/empleados/{luis}/baja", headers=H(clave), json={"fecha_cese": "2026-10-31", "motivo": "02"}).status_code == 422
    assert cli.post(f"{B}/empleados/{luis}/baja", headers=H(clave),
                    json={"fecha_cese": "2026-10-31", "motivo": "02", "clase_despido": "ARBITRARIO"}).status_code == 200
    assert cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave)).status_code == 200
    return clave, B, ana, luis, pids


def _vista(cli, clave, B, pid, emp, cts=None):
    url = f"{B}/liquidaciones/vista?periodo_id={pid}&empleado_id={emp}" + ("" if cts is None else f"&cts_anterior_pagada={str(cts).lower()}")
    r = cli.get(url, headers=H(clave))
    assert r.status_code == 200, r.text
    return r.json()


def _conceptos(v):
    return {c["codigo"]: D(str(c["monto"])) for c in v["conceptos"]}


def _cerrar(cli, clave, B, pid, emp, cts=True):
    return cli.post(f"{B}/liquidaciones/cerrar", headers=H(clave), json={"periodo_id": pid, "empleado_id": emp, "cts_anterior_pagada": cts})


def test_ver_calcula_sin_guardar_y_los_numeros_del_documento(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    lista = cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(clave)).json()
    assert lista["resumen"] == {"sin_cerrar": 2, "cerradas": 0}
    assert all(f["estado"] == "SIN_CERRAR" and f["origen"] == "MES" and f["neto_a_pagar"] is None for f in lista["filas"])

    v = _vista(cli, clave, B, pids[10], ana, True)
    c = _conceptos(v)
    assert v["estado"] == "SIN_CERRAR" and v["liquidacion_id"] is None
    assert c["@GRATIFICACION"] == D("1556.50") and c["@BONIF_EXTRA_29351"] == D("140.09")
    assert c["@CTS_TRUNCA"] == D("1715.03")                                           # 1,513.26 + 201.77
    assert c["@VACACIONES_VENCIDAS"] == D("1556.50") and c["@VACACIONES_TRUNCAS"] == D("1988.86")
    assert c["@FONDO_PENSIONES"] == D("354.54") and c["@COMISION_VARIABLE"] == D("56.73") and c["@SEGURO_INVALIDEZ"] == D("48.57")
    assert sum(c[k] for k in ("@GRATIFICACION", "@BONIF_EXTRA_29351", "@CTS_TRUNCA", "@VACACIONES_VENCIDAS", "@VACACIONES_TRUNCAS")) == D("6956.98")
    # la devolución de 5ta es un OTRO INGRESO (como en el formato de la empresa), nunca un descuento negativo
    assert c["@DEVOLUCION_5TA"] > 0 and "@IMP_RENTA_5TA" not in c and all(D(str(x["monto"])) >= 0 for x in v["conceptos"])
    assert D(str(v["neto_a_pagar"])) == D(str(v["total_ingresos"])) - D(str(v["total_descuentos"]))
    assert v["plazo_pago"] == "2026-10-22" and v["puede_cerrar"]
    assert v["snapshot"]["pension"]["tipo_comision"] == "FLUJO"
    # NO quedó nada guardado: la lista sigue igual y no hay neto ni id
    despues = cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(clave)).json()
    assert despues["resumen"] == {"sin_cerrar": 2, "cerradas": 0} and all(f["liquidacion_id"] is None for f in despues["filas"])


def test_cts_anterior_sin_responder_bloquea_el_cierre(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    v = _vista(cli, clave, B, pids[10], ana)
    assert v["cts_anterior_aplica"] and v["cts_anterior_pagada"] is None and not v["puede_cerrar"] and v["faltantes_para_cerrar"]
    assert _cerrar(cli, clave, B, pids[10], ana, None).status_code == 409
    no_pagada = _vista(cli, clave, B, pids[10], ana, False)
    cts = [c for c in no_pagada["conceptos"] if c["codigo"] == "@CTS_TRUNCA"]
    assert sorted(D(str(c["monto"])) for c in cts) == [D("1556.50"), D("1715.03")]
    assert _vista(cli, clave, B, pids[10], ana, True)["puede_cerrar"]


def test_indemnizacion_por_despido_arbitrario(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    c = _conceptos(_vista(cli, clave, B, pids[10], luis, True))
    assert c["@INDEMNIZACION_DESPIDO"] == D("2250.00")                                # 1.5 × 2,000 × 9/12
    assert c["@GRATIFICACION"] == D("1333.33") and c["@BONIF_EXTRA_29351"] == D("120.00")
    assert c["@VACACIONES_TRUNCAS"] == D("1500.00") and c["@CTS_TRUNCA"] == D("1138.89")
    assert "@FONDO_PENSIONES" not in c and c["@SNP_ONP"] == D("195.00")


def test_cerrar_guarda_bloquea_y_reabrir_vuelve_a_ser_un_calculo(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    antes = [f for f in cli.get(f"{B}/planillas/{pids[10]}/resultados", headers=H(clave)).json()["filas"] if f["documento"] == "45678912"][0]
    cerrada = _cerrar(cli, clave, B, pids[10], ana).json()
    assert cerrada["estado"] == "CERRADA" and cerrada["liquidacion_id"]
    lista = {f["documento"]: f for f in cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(clave)).json()["filas"]}
    assert lista["45678912"]["estado"] == "CERRADA" and lista["45678912"]["neto_a_pagar"] is not None and lista["40123456"]["estado"] == "SIN_CERRAR"
    assert _cerrar(cli, clave, B, pids[10], ana).status_code == 409                                      # ya cerrada
    # «Ver» de una cerrada devuelve lo guardado (no recalcula)
    assert _vista(cli, clave, B, pids[10], ana)["estado"] == "CERRADA"
    # la boleta del colaborador queda bloqueada
    rec = cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave)).json()
    assert rec["bloqueados_por_liquidacion"] == 1
    despues = [f for f in cli.get(f"{B}/planillas/{pids[10]}/resultados", headers=H(clave)).json()["filas"] if f["documento"] == "45678912"][0]
    assert despues["neto"] == antes["neto"] and despues["liquidacion_estado"] == "CERRADA"
    assert D(str(despues["total_consolidado"])) == D(str(despues["neto"])) + D(str(despues["liquidacion_neto"]))
    assert cli.post(f"{B}/empleados/{ana}/sueldos", headers=H(clave), json={"monto": 3500, "fecha_vigencia": "2026-10-01"}).status_code == 403
    assert cli.post(f"{B}/empleados/{ana}/ausentismos", headers=H(clave), json={"codigo_plame": "06", "fecha_inicio": "2026-10-02", "fecha_fin": "2026-10-02"}).status_code == 403
    assert cli.post(f"{B}/empleados/{ana}/reactivar-por-error", headers=H(clave)).status_code == 403
    nov = cli.put(f"{B}/periodos/{pids[10]}/novedades", headers=H(clave), json=[{"documento": "45678912", "codigo": "@COMISIONES", "valor": "10"}])
    assert nov.status_code == 422 and "CERRADA" in nov.json()["errores"][0]["error"]
    lid = cerrada["liquidacion_id"]
    assert cli.post(f"{B}/liquidaciones/{lid}/reabrir", headers=H(clave), json={"motivo": "ok"}).status_code == 422
    assert cli.post(f"{B}/liquidaciones/{lid}/reabrir", headers=H(clave), json={"motivo": "Corrección de vacaciones"}).json()["estado"] == "SIN_CERRAR"
    lista = {f["documento"]: f for f in cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(clave)).json()["filas"]}
    assert lista["45678912"]["estado"] == "SIN_CERRAR" and lista["45678912"]["liquidacion_id"] is None       # volvió a ser un cálculo
    assert cli.post(f"{B}/empleados/{ana}/sueldos", headers=H(clave), json={"monto": 3500, "fecha_vigencia": "2026-10-01"}).status_code == 201


def test_un_calculo_sin_cerrar_no_estorba_la_reactivacion(entorno):
    """El conflicto que se reportó: ver, exportar o recalcular una liquidación no debe impedir reactivar al colaborador."""
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    _vista(cli, clave, B, pids[10], ana, True)
    assert cli.get(f"{B}/liquidaciones/pdf?periodo_id={pids[10]}&empleado_id={ana}&cts_anterior_pagada=true", headers=H(clave)).status_code == 200
    assert cli.get(f"{B}/liquidaciones/excel?periodo_id={pids[10]}&empleado_id={ana}&cts_anterior_pagada=true", headers=H(clave)).status_code == 200
    assert cli.post(f"{B}/empleados/{ana}/reactivar-por-error", headers=H(clave)).status_code == 200
    assert [f for f in cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(clave)).json()["filas"] if f["documento"] == "45678912"] == []
    # y el reingreso de otro cesado tampoco choca
    assert cli.post(f"{B}/empleados/{luis}/reingreso", headers=H(clave), json={"fecha_ingreso": "2026-11-05"}).status_code == 200


def test_solo_lo_cerrado_viaja_al_afp_plame_y_resumenes(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    rem = lambda z: zipfile.ZipFile(io.BytesIO(z.content)).read([n for n in zipfile.ZipFile(io.BytesIO(z.content)).namelist() if n.endswith("rem")][0]).decode("latin-1")

    # --- con todo SIN CERRAR (aunque se haya visto, exportado o calculado): no viaja nada
    _vista(cli, clave, B, pids[10], ana, True)
    afp = cli.get(f"{B}/planillas/{pids[10]}/exportar-afpnet?formato=csv", headers=H(clave))
    fila = [l.split(",") for l in afp.text.strip().splitlines()][0]
    assert fila[3] == "45678912" and float(fila[11]) == 2075.33                                        # solo la boleta del mes
    assert "liquidaciones-sin-cerrar=2" in afp.headers["x-avisos"] and "incluidas=0" in afp.headers["x-avisos"]
    z = cli.get(f"{B}/planillas/{pids[10]}/exportar-plame-zip", headers=H(clave))
    assert not any(c in rem(z) for c in ("|0118|3545.36", "|0406|1556.50", "|0312|140.09"))
    res = cli.get(f"{B}/planillas/{pids[10]}/resumen-conceptos", headers=H(clave)).json()
    assert res["liquidaciones_incluidas"] == 0 and not any(c["codigo"] in ("@CTS_TRUNCA", "@VACACIONES_TRUNCAS") for c in res["conceptos"])
    base_neto = D(str(res["neto_total"]))
    fila_res = [f for f in cli.get(f"{B}/planillas/{pids[10]}/resultados", headers=H(clave)).json()["filas"] if f["documento"] == "45678912"][0]
    assert fila_res["liquidacion_neto"] is None and fila_res["total_consolidado"] is None

    # --- al CERRAR Ana, y solo entonces, entra en todo, en UN solo archivo
    cerrada = _cerrar(cli, clave, B, pids[10], ana).json()
    afp = cli.get(f"{B}/planillas/{pids[10]}/exportar-afpnet?formato=csv", headers=H(clave))
    filas = [l.split(",") for l in afp.text.strip().splitlines()]
    assert len(filas) == 1 and float(filas[0][11]) == 5620.69                                           # boleta 2,075.33 + vacaciones 3,545.36
    assert "liquidaciones-incluidas=1" in afp.headers["x-avisos"] and "liquidaciones-sin-cerrar=1" in afp.headers["x-avisos"]
    z = cli.get(f"{B}/planillas/{pids[10]}/exportar-plame-zip", headers=H(clave))
    lineas = [l for l in rem(z).splitlines() if l.startswith("01|45678912|")]
    codigos = {l.split("|")[2]: l.split("|")[3] for l in lineas}
    assert codigos["0118"] == "3545.36" and codigos["0406"] == "1556.50" and codigos["0312"] == "140.09" and codigos["0121"] == "2000.00"
    assert len(lineas) == len(codigos) and "@CTS_TRUNCA" in z.headers["x-avisos"]
    res = cli.get(f"{B}/planillas/{pids[10]}/resumen-conceptos", headers=H(clave)).json()
    assert res["liquidaciones_incluidas"] == 1 and any(c["codigo"] == "@VACACIONES_TRUNCAS" for c in res["conceptos"])
    assert D(str(res["neto_total"])) == base_neto + D(str(cerrada["neto_a_pagar"]))
    # y al reabrir vuelve a salir de todo
    cli.post(f"{B}/liquidaciones/{cerrada['liquidacion_id']}/reabrir", headers=H(clave), json={"motivo": "Reapertura de prueba"})
    afp = cli.get(f"{B}/planillas/{pids[10]}/exportar-afpnet?formato=csv", headers=H(clave))
    assert float([l.split(",") for l in afp.text.strip().splitlines()][0][11]) == 2075.33


def test_pdf_y_excel_borrador_y_definitivo(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    q = f"periodo_id={pids[10]}&empleado_id={ana}&cts_anterior_pagada=true"
    borrador = cli.get(f"{B}/liquidaciones/pdf?{q}", headers=H(clave))
    assert borrador.status_code == 200 and borrador.content.startswith(b"%PDF") and "BORRADOR" in borrador.headers["content-disposition"]
    xl = cli.get(f"{B}/liquidaciones/excel?{q}", headers=H(clave))
    assert xl.status_code == 200 and xl.content[:2] == b"PK" and "BORRADOR" in xl.headers["content-disposition"]
    con_formulas = cli.get(f"{B}/liquidaciones/pdf?{q}&formulas=true", headers=H(clave))
    assert len(con_formulas.content) > len(borrador.content)                                            # agrega la hoja de detalle del cálculo
    _cerrar(cli, clave, B, pids[10], ana)
    definitivo = cli.get(f"{B}/liquidaciones/pdf?periodo_id={pids[10]}&empleado_id={ana}", headers=H(clave))
    assert definitivo.content.startswith(b"%PDF") and "BORRADOR" not in definitivo.headers["content-disposition"]
    assert "BORRADOR" not in cli.get(f"{B}/liquidaciones/excel?periodo_id={pids[10]}&empleado_id={ana}", headers=H(clave)).headers["content-disposition"]


def test_periodo_cerrado_permite_ver_pero_no_cerrar(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    assert cli.post(f"{B}/planillas/{pids[10]}/cerrar", headers=H(clave)).status_code == 200
    assert _vista(cli, clave, B, pids[10], luis, True)["puede_cerrar"] is False                         # se puede ver, no cerrar
    r = _cerrar(cli, clave, B, pids[10], luis)
    assert r.status_code == 409 and "CERRADO" in r.json()["detail"]


def test_pendientes_de_meses_anteriores_y_aislamiento(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    nov = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 11}, headers=H(clave)).json()["id"]
    lista = cli.get(f"{B}/liquidaciones?periodo_id={nov}", headers=H(clave)).json()
    assert {(f["documento"], f["estado"], f["origen"]) for f in lista["filas"]} == {("45678912", "SIN_CERRAR", "PENDIENTE_ANTERIOR"), ("40123456", "SIN_CERRAR", "PENDIENTE_ANTERIOR")}
    otra = nueva("Otra cuenta")
    assert cli.get(f"{B}/liquidaciones?periodo_id={pids[10]}", headers=H(otra)).status_code == 404
    r = cli.get(f"{B}/liquidaciones/vista?periodo_id={pids[9]}&empleado_id={ana}", headers=H(clave))
    assert r.status_code == 422 and "no cae" in r.json()["detail"]
    # un cesado cerrado deja de aparecer como pendiente en los meses siguientes
    _cerrar(cli, clave, B, pids[10], ana)
    lista = cli.get(f"{B}/liquidaciones?periodo_id={nov}", headers=H(clave)).json()
    assert [f["documento"] for f in lista["filas"]] == ["40123456"]
