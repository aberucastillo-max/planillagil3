"""Carga masiva de novedades del periodo por Excel: revisión previa, todo o nada, actualización de las existentes."""
import base64
import io
from decimal import Decimal as D

from openpyxl import Workbook, load_workbook

from tests.test_liquidacion import H, _escenario

URL = lambda B, pid: f"{B}/periodos/{pid}/novedades/carga-masiva"


def _xlsx(filas, encabezados=("Año", "Mes", "DNI *", "Código del concepto *", "Monto (S/) *")) -> str:
    wb = Workbook(); ws = wb.active; ws.title = "Novedades"
    for j, h in enumerate(encabezados, 1):
        ws.cell(1, j, h)
    for i, f in enumerate(filas, 2):
        for j, v in enumerate(f, 1):
            ws.cell(i, j, v)
    buf = io.BytesIO(); wb.save(buf)
    return base64.b64encode(buf.getvalue()).decode()


def _nov(cli, clave, B, pid):
    return {(n["documento"], n["codigo"]): D(str(n["valor"])) for n in cli.get(f"{B}/periodos/{pid}/novedades", headers=H(clave)).json()}


def test_plantilla_con_los_conceptos_que_admiten_carga(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    r = cli.get(f"{URL(B, pids[10])}/plantilla", headers=H(clave))
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"] and "PLANTILLA_NOVEDADES_202610" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Léeme", "Novedades", "Conceptos", "Colaboradores"]
    codigos = [c.value for c in wb["Conceptos"]["A"]][1:]
    assert "@COMISIONES" in codigos and "@HABER_BASICO" not in codigos                       # solo los que el motor no calcula
    assert [c.value for c in wb["Novedades"][1]][2:] == ["DNI *", "Código del concepto *", "Monto (S/) *"]


def test_cargar_novedades_con_revision_previa_y_actualizacion(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    pid = pids[10]
    otro = cli.post(f"{B}/empleados", headers=H(clave), json={"nombres": "Mario", "apellidos": "Novedad", "documento_identidad": "47001122", "fecha_ingreso": "2025-01-02",
                    "sueldo_basico": 3000, "sistema_pensionario": "ONP"}).json()
    filas = [[2026, "Octubre", "47001122", "@COMISIONES", 350.5], [None, None, "47001122", "descuentos_otros", "20,25"]]    # sin «@» y con coma decimal
    v = cli.post(f"{URL(B, pid)}/validar", headers=H(clave), json={"archivo_base64": _xlsx(filas)}).json()
    assert v["puede_cargar"] and v["resumen"]["nuevas"] == 2 and v["filas"][1]["codigo"] == "@DESCUENTOS_OTROS" and v["filas"][1]["monto"] == "20.25"
    assert _nov(cli, clave, B, pid) == {}                                                       # revisar no guarda nada
    r = cli.post(f"{URL(B, pid)}/cargar", headers=H(clave), json={"archivo_base64": _xlsx(filas)}).json()
    assert r["cargado"] and r["guardadas"] == 2 and r["nuevas"] == 2
    assert _nov(cli, clave, B, pid) == {("47001122", "@COMISIONES"): D("350.50"), ("47001122", "@DESCUENTOS_OTROS"): D("20.25")}
    act = cli.post(f"{URL(B, pid)}/cargar", headers=H(clave), json={"archivo_base64": _xlsx([[None, None, "47001122", "@COMISIONES", 400]])}).json()
    assert act["cargado"] and act["actualizadas"] == 1 and act["informe"]["filas"][0]["accion"] == "ACTUALIZAR" and act["informe"]["filas"][0]["antes"] == "350.50"
    assert _nov(cli, clave, B, pid)[("47001122", "@COMISIONES")] == D("400.00")


def test_todo_o_nada_y_cada_error_con_su_fila(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    pid = pids[10]
    cli.post(f"{B}/empleados", headers=H(clave), json={"nombres": "Mario", "apellidos": "Novedad", "documento_identidad": "47001122", "fecha_ingreso": "2025-01-02", "sueldo_basico": 3000, "sistema_pensionario": "ONP"})
    filas = [[None, None, "47001122", "@COMISIONES", 100],              # fila 2: válida
             [None, None, "99999999", "@COMISIONES", 100],              # 3: DNI inexistente
             [None, None, "47001122", "@NOEXISTE", 100],                # 4: código inválido
             [None, None, "47001122", "@HABER_BASICO", 100],            # 5: concepto calculado
             [None, None, "47001122", "@HEXT_25", "10.555"],            # 6: más de 2 decimales
             [None, None, "47001122", "@COMISIONES", 50],               # 7: repetida
             [2025, None, "47001122", "@SUMA_GRACIOSA", 10],            # 8: año de otro periodo
             [None, "Setiembre", "47001122", "@SUMA_GRACIOSA", 10]]     # 9: mes de otro periodo
    r = cli.post(f"{URL(B, pid)}/cargar", headers=H(clave), json={"archivo_base64": _xlsx(filas)}).json()
    assert r["cargado"] is False and r["informe"]["resumen"]["con_error"] == 7 and r["informe"]["puede_cargar"] is False
    assert _nov(cli, clave, B, pid) == {}                                                       # ni siquiera la fila válida se guardó
    err = {f["fila"]: " ".join(f["errores"]) for f in r["informe"]["filas"]}
    assert not err[2] and "inexistente" in err[3] and "inválido" in err[4] and "calculado" in err[5] and "2 decimales" in err[6]
    assert "repetidos" in err[7] and "año" in err[8] and "mes" in err[9]


def test_periodo_cerrado_colaborador_bloqueado_y_archivos_invalidos(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    c = cli.post(f"{B}/liquidaciones/cerrar", headers=H(clave), json={"periodo_id": pids[10], "empleado_id": luis, "cts_anterior_pagada": True})
    assert c.status_code in (200, 201), c.text                                                  # cierra la liquidación de Luis: su boleta queda bloqueada
    cerradas = [luis]
    doc = next(e["documento"] for e in cli.get(f"{B}/empleados", headers=H(clave)).json() if e["id"] == cerradas[0])
    r = cli.post(f"{URL(B, pids[10])}/validar", headers=H(clave), json={"archivo_base64": _xlsx([[None, None, doc, "@COMISIONES", 10]])}).json()
    assert r["puede_cargar"] is False and "CERRADA" in " ".join(r["filas"][0]["errores"])        # su boleta está bloqueada
    assert cli.post(f"{B}/planillas/{pids[8]}/cerrar", headers=H(clave)).status_code == 200
    assert cli.post(f"{URL(B, pids[8])}/validar", headers=H(clave), json={"archivo_base64": _xlsx([[None, None, "45678912", "@COMISIONES", 10]])}).status_code == 403   # periodo cerrado
    assert cli.post(f"{URL(B, pids[10])}/validar", headers=H(clave), json={"archivo_base64": base64.b64encode(b"no es un excel").decode()}).status_code == 422
    falta = cli.post(f"{URL(B, pids[10])}/validar", headers=H(clave), json={"archivo_base64": _xlsx([[None, None, "45678912", "@COMISIONES", 10]], encabezados=("Año", "Mes", "DNI *", "Código del concepto *", "Importe"))})
    assert falta.status_code == 422 and "Monto" in falta.json()["detail"]
