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
