"""Datos adicionales de la liquidación: ingresos y descuentos manuales (suma graciosa, descuentos otros...) ligados al Diccionario."""
import io
import zipfile
from decimal import Decimal as D

from tests.test_liquidacion import API, H, _cerrar, _conceptos, _escenario, _vista

M = lambda B: f"{B}/liquidaciones/manuales"


def _agregar(cli, clave, B, emp, codigo, monto, **k):
    return cli.post(M(B), headers=H(clave), json={"empleado_id": emp, "concepto_codigo": codigo, "monto": monto, **k})


def test_catalogo_ligado_a_la_tabla_de_conceptos(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    r = cli.get(f"{M(B)}?empleado_id={ana}", headers=H(clave)).json()
    cat = {c["codigo"]: c for c in r["catalogo"]}
    assert {"@SUMA_GRACIOSA", "@DESCUENTOS_OTROS", "@COMISIONES", "@QUINCENA"} <= set(cat)
    assert cat["@SUMA_GRACIOSA"]["categoria"] == "INGRESO" and cat["@DESCUENTOS_OTROS"]["categoria"] == "DESCUENTO"
    assert (cat["@SUMA_GRACIOSA"]["afecta_5ta"], cat["@SUMA_GRACIOSA"]["afecta_afp"], cat["@SUMA_GRACIOSA"]["afecta_essalud"]) == (True, False, False)
    assert not {"@GRATIFICACION", "@CTS_TRUNCA", "@FONDO_PENSIONES", "@HABER_BASICO"} & set(cat)          # lo que el motor calcula no se carga a mano
    assert r["filas"] == [] and r["bloqueado"] is False and r["fecha_cese"] == "2026-10-20"
    primero = lambda cat_: [c["codigo"] for c in r["catalogo"] if c["categoria"] == cat_][0]
    assert primero("INGRESO") == "@SUMA_GRACIOSA" and primero("DESCUENTO") == "@DESCUENTOS_OTROS"       # los genéricos salen primero


def test_suma_graciosa_y_descuentos_otros_participan_en_el_calculo_global(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    base = _vista(cli, clave, B, pids[10], ana, True)
    r = _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 1500, descripcion="Suma graciosa por cese", nota="Acta del 20/10")
    assert r.status_code == 201 and r.json()["filas"][0]["descripcion"] == "Suma graciosa por cese" and D(str(r.json()["total_ingresos"])) == D("1500")
    r = _agregar(cli, clave, B, ana, "@DESCUENTOS_OTROS", 200.50, descripcion="Préstamo de la empresa")
    assert r.status_code == 201 and D(str(r.json()["total_descuentos"])) == D("200.50")
    v = _vista(cli, clave, B, pids[10], ana, True)
    c = _conceptos(v)
    assert c["@SUMA_GRACIOSA"] == D("1500.00") and c["@DESCUENTOS_OTROS"] == D("200.50")
    # participan en los totales y en el neto. Como la suma graciosa afecta a 5ta, parte de ella se va en el impuesto: baja la devolución de 5ta
    aj = lambda x: D(x["snapshot"]["quinta"]["ajuste"])
    assert aj(v) > aj(base)                                                                  # más impuesto (o menos devolución)
    assert D(str(v["total_descuentos"])) == D(str(base["total_descuentos"])) + D("200.50")
    assert D(str(v["neto_a_pagar"])) == D(str(base["neto_a_pagar"])) + D("1500") - D("200.50") - (aj(v) - aj(base))
    assert D(str(v["total_ingresos"])) == D(str(base["total_ingresos"])) + D("1500") - (aj(v) - aj(base))      # la devolución es un ingreso
    # la suma graciosa afecta a 5ta (decisión del dueño) pero no a AFP/ONP ni EsSalud: entra a la renta bruta, no a los aportes
    assert c["@FONDO_PENSIONES"] == _conceptos(base)["@FONDO_PENSIONES"]
    assert D(v["snapshot"]["quinta"]["detalle"]["subtotales"]["liquidacion"]) == D(base["snapshot"]["quinta"]["detalle"]["subtotales"]["liquidacion"]) + D("1500")
    assert D(v["snapshot"]["essalud"]["base"]) == D(base["snapshot"]["essalud"]["base"])
    fila = [x for x in v["conceptos"] if x["codigo"] == "@SUMA_GRACIOSA"][0]
    assert fila["manual"] is True and fila["descripcion"] == "Suma graciosa por cese" and "Acta del 20/10" in fila["formula"] and fila["habilitado"]
    assert [m["descripcion"] for m in v["snapshot"]["manuales"]] == ["Suma graciosa por cese", "Préstamo de la empresa"]
    # el check «Incluir» también funciona sobre ellos (simulación)
    sim = cli.get(f"{B}/liquidaciones/vista?periodo_id={pids[10]}&empleado_id={ana}&cts_anterior_pagada=true&excluir=@SUMA_GRACIOSA", headers=H(clave)).json()
    assert sim["simulacion"] and D(str(sim["total_ingresos"])) == D(str(base["total_ingresos"]))


def test_las_afectaciones_del_dato_adicional_llegan_a_las_bases(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    v0 = _vista(cli, clave, B, pids[10], ana, True)
    base, c_base = _conceptos(v0), D(v0["snapshot"]["quinta"]["detalle"]["subtotales"]["liquidacion"])
    # @COMISIONES por defecto afecta AFP, EsSalud y 5ta (según el Diccionario)
    _agregar(cli, clave, B, ana, "@COMISIONES", 1000)
    v = _vista(cli, clave, B, pids[10], ana, True)
    assert _conceptos(v)["@FONDO_PENSIONES"] == base["@FONDO_PENSIONES"] + D("100.00")                    # 10 % de 1,000
    assert D(v["snapshot"]["quinta"]["detalle"]["subtotales"]["liquidacion"]) > 0
    assert D(v["snapshot"]["essalud"]["base"]) >= D("1000")
    # la suma graciosa puede marcarse como afecta a 5ta (decisión del usuario, validar con el contador)
    mid = cli.get(f"{M(B)}?empleado_id={ana}", headers=H(clave)).json()["filas"][0]["id"]
    assert cli.delete(f"{M(B)}/{mid}?empleado_id={ana}", headers=H(clave)).status_code == 200
    _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 2000, afecta_5ta=True)
    v2 = _vista(cli, clave, B, pids[10], ana, True)
    assert D(v2["snapshot"]["quinta"]["detalle"]["subtotales"]["liquidacion"]) == c_base + D("2000")     # entra a la base de 5ta (renta bruta)
    assert any(c["codigo"] == "@SUMA_GRACIOSA" for c in v2["snapshot"]["quinta"]["detalle"]["liquidacion"])
    assert _conceptos(v2)["@FONDO_PENSIONES"] == base["@FONDO_PENSIONES"]                                  # sigue sin afectar AFP


def test_agregar_editar_y_borrar_con_validaciones(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    assert _agregar(cli, clave, B, ana, "@GRATIFICACION", 100).status_code == 422                          # lo calculado no se carga a mano
    assert _agregar(cli, clave, B, ana, "@NO_EXISTE", 100).status_code == 422
    assert _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 0).status_code == 422
    assert _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", -5).status_code == 422
    activo = cli.post(f"{B}/empleados", headers=H(clave), json={"nombres": "Eva", "apellidos": "Activa", "documento_identidad": "70112233", "fecha_ingreso": "2026-03-01", "sueldo_basico": 2000}).json()["id"]
    assert _agregar(cli, clave, B, activo, "@SUMA_GRACIOSA", 100).status_code == 422                        # solo cesados
    assert _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 800, descripcion="Suma graciosa").status_code == 201
    assert _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 800, descripcion="Suma graciosa").status_code == 409    # doble clic
    fila = cli.get(f"{M(B)}?empleado_id={ana}", headers=H(clave)).json()["filas"][0]
    r = cli.put(f"{M(B)}/{fila['id']}?empleado_id={ana}", headers=H(clave), json={"concepto_codigo": "@DESCUENTOS_OTROS", "descripcion": "Descuento corregido", "monto": 120, "nota": "cambió de tipo"})
    assert r.status_code == 200 and r.json()["filas"][0]["categoria"] == "DESCUENTO" and r.json()["filas"][0]["afecta_5ta"] is False
    assert D(str(r.json()["total_descuentos"])) == D("120") and D(str(r.json()["total_ingresos"])) == 0
    assert cli.put(f"{M(B)}/{fila['id']}?empleado_id={luis}", headers=H(clave), json={"concepto_codigo": "@SUMA_GRACIOSA", "monto": 1}).status_code == 404   # de otro colaborador
    assert cli.delete(f"{M(B)}/{fila['id']}?empleado_id={ana}", headers=H(clave)).json()["filas"] == []
    assert cli.delete(f"{M(B)}/{fila['id']}?empleado_id={ana}", headers=H(clave)).status_code == 404
    assert cli.get(f"{M(B)}?empleado_id={ana}", headers=H(nueva('Otra'))).status_code == 404               # aislamiento entre cuentas


def test_cerrar_guarda_los_datos_adicionales_y_reabrir_los_libera(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 1500, descripcion="Suma graciosa por cese")
    _agregar(cli, clave, B, ana, "@DESCUENTOS_OTROS", 200, descripcion="Descuento otros")
    previo = _vista(cli, clave, B, pids[10], ana, True)
    cerr = _cerrar(cli, clave, B, pids[10], ana).json()
    assert cerr["estado"] == "CERRADA" and D(str(cerr["neto_a_pagar"])) == D(str(previo["neto_a_pagar"]))
    manuales = [c for c in cerr["conceptos"] if c["manual"]]
    assert {(c["codigo"], c["descripcion"], c["categoria"]) for c in manuales} == {("@SUMA_GRACIOSA", "Suma graciosa por cese", "INGRESO"), ("@DESCUENTOS_OTROS", "Descuento otros", "DESCUENTO")}
    # cerrada: no se puede agregar, editar ni borrar
    lista = cli.get(f"{M(B)}?empleado_id={ana}", headers=H(clave)).json()
    assert lista["bloqueado"] is True and len(lista["filas"]) == 2
    mid = lista["filas"][0]["id"]
    assert _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 5).status_code == 403
    assert cli.put(f"{M(B)}/{mid}?empleado_id={ana}", headers=H(clave), json={"concepto_codigo": "@SUMA_GRACIOSA", "monto": 9}).status_code == 403
    assert cli.delete(f"{M(B)}/{mid}?empleado_id={ana}", headers=H(clave)).status_code == 403
    # viajan al PLAME con su concepto; sin código PLAME se avisa
    z = cli.get(f"{B}/planillas/{pids[10]}/exportar-plame-zip", headers=H(clave))
    assert "@SUMA_GRACIOSA" in z.headers["x-avisos"] and "@DESCUENTOS_OTROS" in z.headers["x-avisos"]
    # al reabrir vuelven a ser editables y siguen ahí
    cli.post(f"{B}/liquidaciones/{cerr['liquidacion_id']}/reabrir", headers=H(clave), json={"motivo": "Cambio de monto"})
    lista = cli.get(f"{M(B)}?empleado_id={ana}", headers=H(clave)).json()
    assert lista["bloqueado"] is False and len(lista["filas"]) == 2
    assert cli.delete(f"{M(B)}/{mid}?empleado_id={ana}", headers=H(clave)).status_code == 200


def test_pdf_y_excel_incluyen_los_datos_adicionales(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 1500, descripcion="Suma graciosa por cese")
    _agregar(cli, clave, B, ana, "@DESCUENTOS_OTROS", 200, descripcion="Descuento otros")
    q = f"periodo_id={pids[10]}&empleado_id={ana}&cts_anterior_pagada=true"
    pdf = cli.get(f"{B}/liquidaciones/pdf?{q}", headers=H(clave))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(cli.get(f"{B}/liquidaciones/excel?{q}", headers=H(clave)).content))
    texto = " ".join(str(c.value) for fila in wb["Liquidación"].iter_rows() for c in fila if c.value)
    assert "Suma graciosa por cese" in texto and "Descuento otros" in texto


def test_los_datos_adicionales_se_descartan_si_el_cese_se_anula(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 1500)
    assert cli.post(f"{B}/empleados/{ana}/reactivar-por-error", headers=H(clave)).status_code == 200
    cli.post(f"{B}/empleados/{ana}/baja", headers=H(clave), json={"fecha_cese": "2026-10-25", "motivo": "01"})
    assert cli.get(f"{M(B)}?empleado_id={ana}", headers=H(clave)).json()["filas"] == []                    # el cese nuevo parte limpio
    _agregar(cli, clave, B, luis, "@DESCUENTOS_OTROS", 50)
    assert cli.post(f"{B}/empleados/{luis}/reingreso", headers=H(clave), json={"fecha_ingreso": "2026-11-05"}).status_code == 200
    cli.post(f"{B}/empleados/{luis}/baja", headers=H(clave), json={"fecha_cese": "2026-11-30", "motivo": "01"})
    assert cli.get(f"{M(B)}?empleado_id={luis}", headers=H(clave)).json()["filas"] == []


def test_la_vista_indica_a_que_afecta_cada_concepto(entorno):
    """La pantalla muestra, por concepto, si afecta a AFP/ONP, EsSalud y 5ta (los descuentos nunca afectan)."""
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    _agregar(cli, clave, B, ana, "@SUMA_GRACIOSA", 1500)
    _agregar(cli, clave, B, ana, "@DESCUENTOS_OTROS", 200.50)
    v = _vista(cli, clave, B, pids[10], ana, True)
    af = {f["codigo"]: f["afecta"] for f in v["conceptos"]}
    assert set(af["@SUMA_GRACIOSA"]) == {"afp", "essalud", "quinta"}
    assert af["@SUMA_GRACIOSA"] == {"afp": False, "essalud": False, "quinta": True}            # decisión del dueño: solo 5ta
    assert af["@GRATIFICACION"]["quinta"] is True
    assert all(not any(f["afecta"].values()) for f in v["conceptos"] if f["categoria"] == "DESCUENTO")
