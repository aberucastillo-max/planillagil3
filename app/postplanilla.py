"""Post Planilla: configuración con periodo de aplicación (códigos PLAME y plan de cuentas) y asiento contable.

Regla de periodo: cada registro rige «desde un mes» y vale hasta que se registre otro más reciente (queda el historial).
Para un periodo se usa el registro con mayor «aplica_desde» menor o igual al mes del periodo. No se puede registrar ni borrar
un periodo de aplicación que ya alcanzó a un periodo CERRADO, para no alterar archivos y asientos de meses cerrados."""
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .diccionario import ErrorDeNegocio
from .models import (ConfigCuenta, ConfigPlame, Concepto, Detalle, Empresa, Liquidacion, LiquidacionDetalle, Periodo, Resultado)

CONCEPTO_NETO = "@NETO_A_PAGAR"
CERO = Decimal("0.00")
TIPOS = {
    "plame": {"modelo": ConfigPlame, "categorias": ("INGRESO", "DESCUENTO")},
    "cuentas": {"modelo": ConfigCuenta, "categorias": ("INGRESO", "DESCUENTO", "APORTE")},
}


def _mes(texto: str) -> date:
    anio, mes = texto.split("-")
    return date(int(anio), int(mes), 1)


def _ref(db: Session, periodo_id: Optional[int], empresa: Empresa) -> date:
    if periodo_id is None:
        hoy = date.today()
        return date(hoy.year, hoy.month, 1)
    p = db.get(Periodo, periodo_id)
    if not p or p.empresa_id != empresa.id:
        raise HTTPException(404, "Periodo no encontrado.")
    return date(p.anio, p.mes, 1)


def ultimo_cerrado(db: Session, empresa_id: int) -> Optional[date]:
    p = (db.query(Periodo).filter(Periodo.empresa_id == empresa_id, Periodo.estado == "CERRADO")
         .order_by(Periodo.anio.desc(), Periodo.mes.desc()).first())
    return date(p.anio, p.mes, 1) if p else None


def vigentes(db: Session, tipo: str, empresa_id: int, mes_ref: date) -> dict:
    """Registro vigente de cada concepto para un mes: el de mayor «aplica_desde» menor o igual a ese mes."""
    M = TIPOS[tipo]["modelo"]
    salida: dict = {}
    for r in (db.query(M).filter(M.empresa_id == empresa_id, M.aplica_desde <= mes_ref).order_by(M.aplica_desde)):
        salida[r.concepto_codigo] = r
    return salida


def _conceptos_dic(db: Session, empresa: Empresa, tipo: str) -> list:
    cats = TIPOS[tipo]["categorias"]
    return (db.query(Concepto).filter(Concepto.diccionario_id == empresa.diccionario_id, Concepto.categoria.in_(cats))
            .order_by(Concepto.categoria, Concepto.codigo).all())


def _fila_hist(r, tipo: str, bloqueado_hasta: Optional[date]) -> dict:
    base = {"id": r.id, "concepto_codigo": r.concepto_codigo, "aplica_desde": r.aplica_desde.strftime("%Y-%m"), "nota": r.nota,
            "creado": r.creado.isoformat() if r.creado else None,
            "editable": not (bloqueado_hasta and r.aplica_desde <= bloqueado_hasta)}
    if tipo == "plame":
        base["codigo_plame"] = r.codigo_plame
    else:
        base.update({"cuenta_debe": r.cuenta_debe, "cuenta_haber": r.cuenta_haber})
    return base


def listar(db: Session, empresa: Empresa, tipo: str, periodo_id: Optional[int] = None) -> dict:
    ref = _ref(db, periodo_id, empresa)
    vig = vigentes(db, tipo, empresa.id, ref)
    cerrado = ultimo_cerrado(db, empresa.id)
    conceptos = []
    for c in _conceptos_dic(db, empresa, tipo):
        v = vig.get(c.codigo)
        fila = {"codigo": c.codigo, "descripcion": c.descripcion, "categoria": c.categoria}
        if tipo == "plame":
            fila.update({"base": c.codigo_plame, "vigente": v.codigo_plame if v else c.codigo_plame,
                         "origen": "EMPRESA" if v else ("DICCIONARIO" if c.codigo_plame else None),
                         "desde": v.aplica_desde.strftime("%Y-%m") if v else None})
        else:
            fila.update({"cuenta_debe": v.cuenta_debe if v else None, "cuenta_haber": v.cuenta_haber if v else None,
                         "desde": v.aplica_desde.strftime("%Y-%m") if v else None})
        conceptos.append(fila)
    if tipo == "cuentas":                                        # el neto a pagar también lleva su cuenta
        v = vig.get(CONCEPTO_NETO)
        conceptos.append({"codigo": CONCEPTO_NETO, "descripcion": "Neto a pagar (planilla y liquidaciones cerradas)", "categoria": "NETO",
                          "cuenta_debe": v.cuenta_debe if v else None, "cuenta_haber": v.cuenta_haber if v else None,
                          "desde": v.aplica_desde.strftime("%Y-%m") if v else None})
    M = TIPOS[tipo]["modelo"]
    hist = db.query(M).filter(M.empresa_id == empresa.id).order_by(M.aplica_desde.desc(), M.concepto_codigo).all()
    return {"referencia": ref.strftime("%Y-%m"), "ultimo_periodo_cerrado": cerrado.strftime("%Y-%m") if cerrado else None,
            "conceptos": conceptos, "historial": [_fila_hist(r, tipo, cerrado) for r in hist]}


def _validar_concepto(db: Session, empresa: Empresa, tipo: str, codigo: str):
    if tipo == "cuentas" and codigo == CONCEPTO_NETO:
        return
    c = db.query(Concepto).filter(Concepto.diccionario_id == empresa.diccionario_id, Concepto.codigo == codigo).first()
    if not c:
        raise HTTPException(422, f"El concepto {codigo} no existe en el Diccionario.")
    if c.categoria not in TIPOS[tipo]["categorias"]:
        raise HTTPException(422, f"El concepto {codigo} es de categoría {c.categoria}: no admite esta configuración.")


def registrar(db: Session, empresa: Empresa, tipo: str, datos) -> dict:
    desde = _mes(datos.aplica_desde)
    _validar_concepto(db, empresa, tipo, datos.concepto_codigo)
    if tipo == "cuentas" and not (datos.cuenta_debe or datos.cuenta_haber):
        raise HTTPException(422, "Indique al menos una cuenta: debe o haber.")
    cerrado = ultimo_cerrado(db, empresa.id)
    if cerrado and desde <= cerrado:
        raise HTTPException(409, f"El periodo de aplicación debe ser posterior a {cerrado.strftime('%m/%Y')}, último periodo cerrado: no se alteran meses cerrados.")
    M = TIPOS[tipo]["modelo"]
    if db.query(M).filter(M.empresa_id == empresa.id, M.concepto_codigo == datos.concepto_codigo, M.aplica_desde == desde).first():
        raise HTTPException(409, f"Ya hay un registro de {datos.concepto_codigo} que aplica desde {datos.aplica_desde}. Elimínelo o use otro mes.")
    if tipo == "plame":
        r = M(empresa_id=empresa.id, concepto_codigo=datos.concepto_codigo, codigo_plame=datos.codigo_plame, aplica_desde=desde, nota=datos.nota)
    else:
        r = M(empresa_id=empresa.id, concepto_codigo=datos.concepto_codigo, cuenta_debe=datos.cuenta_debe, cuenta_haber=datos.cuenta_haber,
              aplica_desde=desde, nota=datos.nota)
    db.add(r)
    db.commit()
    return _fila_hist(r, tipo, cerrado)


def eliminar(db: Session, empresa: Empresa, tipo: str, registro_id: int) -> dict:
    M = TIPOS[tipo]["modelo"]
    r = db.query(M).filter(M.id == registro_id, M.empresa_id == empresa.id).first()
    if not r:
        raise HTTPException(404, "Registro no encontrado.")
    cerrado = ultimo_cerrado(db, empresa.id)
    if cerrado and r.aplica_desde <= cerrado:
        raise HTTPException(409, "Este registro ya aplicó a un periodo cerrado: no se puede eliminar.")
    db.delete(r)
    db.commit()
    return {"ok": True}


def totales_por_concepto(db: Session, periodo: Periodo):
    """Total por concepto de la planilla del periodo + liquidaciones CERRADAS (lo sin cerrar no entra). Devuelve también el neto."""
    filas = (db.query(Detalle.concepto_codigo, Detalle.categoria, func.sum(Detalle.monto))
             .filter(Detalle.periodo_id == periodo.id).group_by(Detalle.concepto_codigo, Detalle.categoria).all())
    acum = {(c, cat): Decimal(str(t)) for c, cat, t in filas}
    neto = Decimal(str(db.query(func.coalesce(func.sum(Resultado.neto_a_pagar), 0)).filter(Resultado.periodo_id == periodo.id).scalar()))
    cerradas = db.query(Liquidacion).filter(Liquidacion.periodo_id == periodo.id, Liquidacion.estado == "CERRADA").all()
    for liq in cerradas:
        neto += Decimal(str(liq.neto_a_pagar))
        for d in db.query(LiquidacionDetalle).filter(LiquidacionDetalle.liquidacion_id == liq.id):
            acum[(d.concepto_codigo, d.categoria)] = acum.get((d.concepto_codigo, d.categoria), CERO) + Decimal(str(d.monto))
    return acum, neto, len(cerradas)


def asiento(db: Session, empresa: Empresa, periodo: Periodo) -> dict:
    if periodo.fase == "Quincena":
        raise ErrorDeNegocio("La quincena es solo un adelanto: no genera asiento. Use el periodo de fin de mes.")
    acum, neto, n_liq = totales_por_concepto(db, periodo)
    cuentas = vigentes(db, "cuentas", empresa.id, date(periodo.anio, periodo.mes, 1))
    desc = {c.codigo: c.descripcion for c in db.query(Concepto).filter(Concepto.diccionario_id == periodo.diccionario_id)}
    items = [(c, cat, t) for (c, cat), t in acum.items() if cat in ("INGRESO", "DESCUENTO", "APORTE")] + [(CONCEPTO_NETO, "NETO", neto)]
    lineas: dict = {}
    sin_cuenta = []
    for codigo, cat, total in sorted(items, key=lambda x: (x[1], x[0])):
        total = total.quantize(CERO)
        if total == 0:
            continue
        cfg = cuentas.get(codigo)
        if not cfg:
            sin_cuenta.append({"codigo": codigo, "descripcion": desc.get(codigo, "Neto a pagar"), "categoria": cat, "total": total})
            continue
        for lado, cta in (("debe", cfg.cuenta_debe), ("haber", cfg.cuenta_haber)):
            if cta:
                fila = lineas.setdefault(cta, {"cuenta": cta, "debe": CERO, "haber": CERO, "conceptos": []})
                fila[lado] += total
                fila["conceptos"].append(codigo)
    out = sorted(lineas.values(), key=lambda f: f["cuenta"])
    td, th = sum((f["debe"] for f in out), CERO), sum((f["haber"] for f in out), CERO)
    return {"periodo": {"id": periodo.id, "anio": periodo.anio, "mes": periodo.mes, "fase": periodo.fase, "estado": periodo.estado},
            "definitivo": periodo.estado == "CERRADO", "liquidaciones_incluidas": n_liq,
            "lineas": out, "total_debe": td, "total_haber": th, "descuadre": (td - th).quantize(CERO), "sin_cuenta": sin_cuenta}
