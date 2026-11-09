"""Exportadores regulatorios: AFPnet (17 columnas) y paquete PLAME (.jor, .snl, .rem).
IMPORTANTE: validar códigos y excepciones contra las especificaciones vigentes de AFPnet y PDT PLAME."""
import csv
import io
import zipfile

from sqlalchemy.orm import Session

from . import reglas as R
from .diccionario import ErrorDeNegocio, cargar_parametros
from . import liquidacion as L
from .models import Ausentismo, Detalle, Empleado, Empresa, Periodo, Resultado, TipoSuspension

AFPNET_EXC_SUBSIDIO_TOTAL = "U"      # TODO validar contra AFPnet
AFPNET_EXC_SIN_LABORAR = "L"         # TODO validar contra AFPnet


def _resultados(db: Session, periodo: Periodo):
    filas = (db.query(Resultado, Empleado).join(Empleado, Empleado.id == Resultado.empleado_id)
             .filter(Resultado.periodo_id == periodo.id).order_by(Empleado.apellidos, Empleado.nombres).all())
    if not filas:
        raise ErrorDeNegocio("El periodo no tiene resultados: ejecute Recalcular primero.", 409)
    return filas


def afpnet_filas(db: Session, empresa: Empresa, periodo: Periodo):
    vac_ids = {d.empleado_id for d in db.query(Detalle.empleado_id).filter(
        Detalle.periodo_id == periodo.id, Detalle.concepto_codigo == "@REMUNERA_VACACIONAL")}
    cerradas, _ = L.consolidado(db, periodo)
    filas, sec = [], 1
    for res, emp in _resultados(db, periodo):
        if emp.sistema_pensionario != "AFP":
            continue
        partes = emp.apellidos.strip().split(" ", 1)
        remun = R.D(res.base_previsional) + cerradas.get(emp.id, {}).get("base_previsional", R.CERO)   # boleta + liquidación
        exc = ""
        if res.dias_subsidio >= 30:
            exc, remun = AFPNET_EXC_SUBSIDIO_TOTAL, R.CERO
        elif res.dias_trabajados == 0 and emp.id not in vac_ids:
            exc, remun = AFPNET_EXC_SIN_LABORAR, R.CERO
        filas.append([
            sec, emp.cuspp or "", "0" if emp.tipo_documento == "DNI" else "1", emp.documento_identidad.strip(),
            partes[0], partes[1] if len(partes) > 1 else "", emp.nombres.strip(), "S",
            "S" if periodo.fecha_inicio <= emp.fecha_ingreso <= periodo.fecha_fin else "N",
            "S" if emp.fecha_cese and periodo.fecha_inicio <= emp.fecha_cese <= periodo.fecha_fin else "N",
            exc, float(R.q2(remun)), 0.00, 0.00, 0.00, "N", ""])
        sec += 1
    return filas


def afpnet_bytes(filas, formato: str):
    if formato == "xlsx":
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "AFPnet"
        for f in filas:
            ws.append(f)
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    buf = io.StringIO()
    csv.writer(buf, delimiter=",").writerows(filas)
    return buf.getvalue().encode("latin-1", errors="replace"), "text/csv"


def plame_zip(db: Session, empresa: Empresa, periodo: Periodo):
    pr = cargar_parametros(db, periodo.diccionario_id, periodo.fecha_inicio)
    prefijo = f"0601{periodo.anio}{periodo.mes:02d}{empresa.ruc}"
    horas_dia = int(pr["HORAS_DIA"])

    jor = [f"01|{emp.documento_identidad.strip()}|{res.dias_trabajados * horas_dia}|0|{int(res.horas_extras)}|0|"
           for res, emp in _resultados(db, periodo)]

    snl = []
    q = (db.query(Ausentismo, Empleado, TipoSuspension)
         .join(Empleado, Empleado.id == Ausentismo.empleado_id)
         .join(TipoSuspension, TipoSuspension.id == Ausentismo.tipo_suspension_id)
         .filter(Empleado.empresa_id == empresa.id, Ausentismo.estado == "REGISTRADO",
                 Ausentismo.fecha_inicio <= periodo.fecha_fin, Ausentismo.fecha_fin >= periodo.fecha_inicio))
    for aus, emp, ts in q:
        dias = R.dias_calendario_solapados(aus.fecha_inicio, aus.fecha_fin, periodo.fecha_inicio, periodo.fecha_fin)
        snl.append(f"01|{emp.documento_identidad.strip()}|{ts.codigo_plame}|{dias}|")

    cerradas, _ = L.consolidado(db, periodo)
    docs = {e.id: e.documento_identidad.strip() for e in db.query(Empleado).filter(Empleado.empresa_id == empresa.id)}
    acum = {}          # (empleado, código PLAME) -> monto
    sin_codigo = set()

    def sumar(emp_id, codigo, categoria, monto):
        c = pr.conceptos.get(codigo)
        if not c or categoria not in ("INGRESO", "DESCUENTO"):
            return
        if not c.codigo_plame:
            if monto != 0:
                sin_codigo.add(codigo)
            return
        acum[(emp_id, c.codigo_plame)] = acum.get((emp_id, c.codigo_plame), R.CERO) + R.D(monto)

    for d in db.query(Detalle).filter(Detalle.periodo_id == periodo.id).order_by(Detalle.empleado_id):
        sumar(d.empleado_id, d.concepto_codigo, d.categoria, d.monto)
    for emp_id, datos in cerradas.items():
        for codigo, categoria, monto in datos["lineas"]:
            sumar(emp_id, codigo, categoria, monto)
    rem = []
    for (emp_id, codigo), monto in sorted(acum.items(), key=lambda kv: (docs.get(kv[0][0], ""), kv[0][1])):
        if monto > 0:
            m = f"{monto:.2f}"
            rem.append(f"01|{docs[emp_id]}|{codigo}|{m}|{m}|")

    def txt(lineas):
        return ("\r\n".join(lineas) + ("\r\n" if lineas else "")).encode("latin-1", errors="replace")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{prefijo}.jor", txt(jor))
        zf.writestr(f"{prefijo}.snl", txt(snl))
        zf.writestr(f"{prefijo}.rem", txt(rem))
    return buf.getvalue(), prefijo, sorted(sin_codigo)


def avisos_consolidacion(db: Session, periodo: Periodo):
    """Solo entra lo CERRADO. Se avisa de los cesados del periodo cuya liquidación aún no está cerrada."""
    cerradas, sin_cerrar = L.consolidado(db, periodo)
    return {"incluidas": len(cerradas), "abiertas": sin_cerrar}
