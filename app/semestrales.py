"""Beneficios semestrales (etapa 3): gratificación de julio y diciembre y CTS de mayo y noviembre.
Las fórmulas son las de la liquidación con meses completos (propuesta aprobada). Los valores salen del Diccionario."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from . import reglas as R
from .calculo import D, sueldo_a_fecha
from .diccionario import CLAVES_LIQUIDACION, ErrorDeNegocio, cargar_parametros
from .liquidacion import _promedio_variables, _ultima_gratificacion
from .models import Auditoria, CtsDeposito, Empleado, Empresa, Periodo

CERO = Decimal("0")


# ------------------------------------------------------------------ gratificación (se paga en la boleta de julio y de diciembre)
def semestre_de_pago(mes_pago: int):
    """Julio paga el semestre enero-junio; diciembre paga julio-diciembre. Otro mes: None."""
    return {7: (1, 6), 12: (7, 12)}.get(mes_pago)


def gratificacion_automatica(db: Session, emp: Empleado, periodo: Periodo, pr) -> Optional[dict]:
    """Gratificación y bonificación extraordinaria del semestre para la boleta del mes de pago. None si no corresponde."""
    sem = semestre_de_pago(periodo.mes)
    if sem is None or periodo.fase != "Fin de Mes" or pr.p.get("GRATIFICACION_AUTOMATICA", Decimal(1)) == 0:
        return None
    m_ini, m_fin = sem
    s_ini = date(periodo.anio, m_ini, 1)
    s_fin = R.fin_de_mes(periodo.anio, m_fin)
    hasta = min(s_fin, emp.fecha_cese) if emp.fecha_cese else s_fin
    meses = R.meses_calendario_completos(max(s_ini, emp.fecha_ingreso), hasta)
    if meses <= 0:
        return None
    corte = periodo.fecha_inicio - timedelta(days=1)                 # remuneración vigente al último día del mes anterior
    sueldo = sueldo_a_fecha(db, emp.id, max(corte, emp.fecha_ingreso))
    if sueldo is None:
        return None
    af = R.q2(pr["RMV"] * pr["ASIG_FAM_PCT"]) if emp.asignacion_familiar else CERO
    variables = _promedio_variables(db, emp, periodo.empresa_id, periodo.anio * 12 + periodo.mes, pr)
    rc = sueldo + af + variables
    grat = R.gratificacion_trunca(rc, meses, pr["GRATI_DIVISOR"])
    pct = pr["BONIF_EXTRA_EPS_PCT"] if emp.cobertura_eps else pr["BONIF_EXTRA_PCT"]
    return {"grat": grat, "bonif": R.bonificacion_extraordinaria(grat, pct), "meses": meses, "rc": rc,
            "formula": f"{rc} / {pr['GRATI_DIVISOR']:.0f} × {meses} meses completos del semestre {s_ini:%d/%m/%Y} a {s_fin:%d/%m/%Y}",
            "formula_bonif": f"{grat} × {pct * 100:.2f} %"}


# ------------------------------------------------------------------ CTS semestral
def semestre_cts(anio: int, codigo: str):
    if codigo == "NOV":
        return date(anio, 5, 1), date(anio, 10, 31)
    if codigo == "MAY":
        return date(anio - 1, 11, 1), date(anio, 4, 30)
    raise ErrorDeNegocio("El semestre debe ser NOV (mayo a octubre) o MAY (noviembre a abril).", 422)


def _fila(db: Session, d: CtsDeposito) -> dict:
    e = db.get(Empleado, d.empleado_id)
    return {"empleado_id": e.id, "documento": e.documento_identidad, "colaborador": f"{e.apellidos}, {e.nombres}",
            "meses": d.meses, "dias": d.dias, "remuneracion_computable": d.remuneracion_computable, "monto": d.monto,
            "estado": d.estado, "fecha_deposito": d.fecha_deposito}


def listar_cts(db: Session, empresa: Empresa, anio: int, codigo: str) -> dict:
    ini, fin = semestre_cts(anio, codigo)
    filas = (db.query(CtsDeposito).filter(CtsDeposito.empresa_id == empresa.id, CtsDeposito.sem_inicio == ini)
             .all())
    filas = sorted((_fila(db, d) for d in filas), key=lambda f: f["colaborador"])
    total = sum((D(f["monto"]) for f in filas), CERO)
    return {"semestre": {"codigo": codigo, "anio": anio, "inicio": ini, "fin": fin, "deposito_hasta_mes": 11 if codigo == "NOV" else 5},
            "filas": filas, "total": total,
            "resumen": {"calculadas": sum(1 for f in filas if f["estado"] == "CALCULADA"),
                        "depositadas": sum(1 for f in filas if f["estado"] == "DEPOSITADA")}}


def calcular_cts(db: Session, empresa: Empresa, anio: int, codigo: str) -> dict:
    """Calcula (o recalcula) la CTS del semestre de los colaboradores vigentes al último día del semestre.
    Los ya DEPOSITADOS no se tocan. Los cesados antes del corte se liquidan con su liquidación por cese."""
    ini, fin = semestre_cts(anio, codigo)
    pr = cargar_parametros(db, empresa.diccionario_id, fin, extra=CLAVES_LIQUIDACION)
    avisos: List[str] = []
    q = db.query(Empleado).filter(Empleado.empresa_id == empresa.id, Empleado.fecha_ingreso <= fin)
    for emp in q.order_by(Empleado.apellidos, Empleado.nombres):
        if emp.fecha_cese is not None and emp.fecha_cese <= fin:
            continue
        previo = db.query(CtsDeposito).filter(CtsDeposito.empleado_id == emp.id, CtsDeposito.sem_inicio == ini).first()
        if previo and previo.estado == "DEPOSITADA":
            continue
        nombre = f"{emp.apellidos}, {emp.nombres}"
        sueldo = sueldo_a_fecha(db, emp.id, fin)
        if sueldo is None:
            avisos.append(f"{nombre}: sin sueldo vigente al {fin:%d/%m/%Y}; no se calculó.")
            continue
        y, m, d = R.tiempo_servicio(max(ini, emp.fecha_ingreso), fin)
        meses, dias = y * 12 + m, d
        if meses == 0 and dias == 0:
            continue
        af = R.q2(pr["RMV"] * pr["ASIG_FAM_PCT"]) if emp.asignacion_familiar else CERO
        variables = _promedio_variables(db, emp, empresa.id, fin.year * 12 + fin.month + 1, pr)
        sexto = R.q2(_ultima_gratificacion(db, emp, empresa.id, fin) / 6)
        if sexto == 0:
            avisos.append(f"{nombre}: sin gratificación registrada; la CTS se calculó sin el sexto de gratificación.")
        rc = sueldo + af + variables + sexto
        por_mes, por_dia = R.cts_monto(rc, meses, dias, pr["CTS_DIVISOR_MES"], pr["CTS_DIVISOR_DIA"])
        snap = {"sueldo": str(sueldo), "asignacion_familiar": str(af), "variables_promedio": str(variables),
                "sexto_gratificacion": str(sexto),
                "formula": f"{rc} / {pr['CTS_DIVISOR_MES']:.0f} × {meses} meses + {rc} / {pr['CTS_DIVISOR_DIA']:.0f} × {dias} días"}
        if previo:
            previo.meses, previo.dias, previo.remuneracion_computable = meses, dias, rc
            previo.monto, previo.snapshot, previo.calculado_en = por_mes + por_dia, snap, datetime.now(timezone.utc)
        else:
            db.add(CtsDeposito(empresa_id=empresa.id, empleado_id=emp.id, sem_inicio=ini, sem_fin=fin, meses=meses,
                               dias=dias, remuneracion_computable=rc, monto=por_mes + por_dia, snapshot=snap))
    db.commit()
    r = listar_cts(db, empresa, anio, codigo)
    r["avisos"] = avisos
    return r


def marcar_depositada(db: Session, empresa: Empresa, anio: int, codigo: str, fecha_deposito: date,
                      empleado_ids: Optional[List[int]], cuenta_id: int) -> dict:
    ini, _ = semestre_cts(anio, codigo)
    q = db.query(CtsDeposito).filter(CtsDeposito.empresa_id == empresa.id, CtsDeposito.sem_inicio == ini,
                                     CtsDeposito.estado == "CALCULADA")
    if empleado_ids:
        q = q.filter(CtsDeposito.empleado_id.in_(empleado_ids))
    filas = q.all()
    if not filas:
        raise ErrorDeNegocio("No hay CTS calculada pendiente de depósito para ese semestre.", 409)
    for d in filas:
        d.estado, d.fecha_deposito = "DEPOSITADA", fecha_deposito
    db.add(Auditoria(cuenta_id=cuenta_id, empresa_id=empresa.id, accion="CTS_DEPOSITADA",
                     detalle=f"{codigo} {anio}: {len(filas)} colaborador(es) el {fecha_deposito}"))
    db.commit()
    return listar_cts(db, empresa, anio, codigo)


def revertir_deposito(db: Session, empresa: Empresa, anio: int, codigo: str, empleado_ids: Optional[List[int]],
                      cuenta_id: int) -> dict:
    ini, _ = semestre_cts(anio, codigo)
    q = db.query(CtsDeposito).filter(CtsDeposito.empresa_id == empresa.id, CtsDeposito.sem_inicio == ini,
                                     CtsDeposito.estado == "DEPOSITADA")
    if empleado_ids:
        q = q.filter(CtsDeposito.empleado_id.in_(empleado_ids))
    filas = q.all()
    if not filas:
        raise ErrorDeNegocio("No hay depósitos que revertir.", 409)
    for d in filas:
        d.estado, d.fecha_deposito = "CALCULADA", None
    db.add(Auditoria(cuenta_id=cuenta_id, empresa_id=empresa.id, accion="CTS_DEPOSITO_REVERTIDO",
                     detalle=f"{codigo} {anio}: {len(filas)} colaborador(es)"))
    db.commit()
    return listar_cts(db, empresa, anio, codigo)


def excel_cts(db: Session, empresa: Empresa, anio: int, codigo: str) -> bytes:
    import io
    import openpyxl
    r = listar_cts(db, empresa, anio, codigo)
    if not r["filas"]:
        raise ErrorDeNegocio("Calcule primero la CTS del semestre.", 409)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"CTS {codigo} {anio}"
    ws.append(["Documento", "Colaborador", "Meses", "Días", "Remuneración computable", "CTS a depositar", "Estado", "Fecha de depósito"])
    for f in r["filas"]:
        ws.append([f["documento"], f["colaborador"], f["meses"], f["dias"], float(f["remuneracion_computable"]),
                   float(f["monto"]), f["estado"], str(f["fecha_deposito"] or "")])
    ws.append(["", "TOTAL", "", "", "", float(r["total"]), "", ""])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
