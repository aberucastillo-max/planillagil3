"""Aceptación legal registrada, páginas públicas y exportar/suprimir los datos de una empresa."""
import json
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from . import legal_textos as T
from .db import get_db
from .models import (Aceptacion, Ausentismo, Auditoria, Beneficio, CcValor, ConfigCuenta, ConfigPlame, ConfigVigencia,
                     CtsDeposito, Detalle, Empleado, Empresa, Liquidacion, LiquidacionDetalle, LiquidacionManual, Novedad,
                     Periodo, PeriodoAFP, PeriodoParametro, RentaPrevia5ta, Resultado, Sueldo, Vacacion, Vinculo)
from .security import Acceso, empresa_actual, exigir_propietario, get_acceso

router = APIRouter()
API = "/api/v1"


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()


def legal_pendiente(db: Session, usuario) -> bool:
    return not db.query(Aceptacion).filter_by(usuario_id=usuario.id, version=T.VERSION).first()


@router.get(API + "/legal/version")
def version():
    return {"version": T.VERSION, "documentos": [{"codigo": c, "titulo": t, "url": f"/legal/{c}"} for c, t in T.TITULOS.items()]}


@router.get("/legal/{codigo}", response_class=HTMLResponse)
def pagina(codigo: str):
    if codigo not in T.SECCIONES:
        raise HTTPException(404, "Documento no encontrado.")
    return HTMLResponse(T.html(codigo))


@router.post(API + "/auth/aceptar-legal")
def aceptar(request: Request, acceso: Acceso = Depends(get_acceso), db: Session = Depends(get_db)):
    u = acceso.usuario
    if not u:
        raise HTTPException(400, "Solo un usuario con sesión puede aceptar los términos.")
    if legal_pendiente(db, u):
        db.add(Aceptacion(usuario_id=u.id, cuenta_id=u.cuenta_id, email=u.email, version=T.VERSION, ip=_ip(request)))
        db.commit()
    return {"ok": True, "version": T.VERSION}


@router.get(API + "/legal/aceptaciones")
def aceptaciones(acceso: Acceso = Depends(exigir_propietario), db: Session = Depends(get_db)):
    q = db.query(Aceptacion)
    if not acceso.admin:
        q = q.filter(Aceptacion.cuenta_id == acceso.cuenta.id)
    return [{"email": a.email, "version": a.version, "documentos": a.documentos, "ip": a.ip, "fecha": a.creado_en}
            for a in q.order_by(Aceptacion.id.desc()).limit(500)]


# ------------------------------------------------------------------ exportar y suprimir
def _fila(obj) -> dict:
    out = {}
    for c in inspect(obj).mapper.column_attrs:
        v = getattr(obj, c.key)
        out[c.key] = float(v) if isinstance(v, Decimal) else (v.isoformat() if isinstance(v, (date, datetime)) else v)
    return out


def _conjuntos(db: Session, empresa: Empresa):
    """(nombre, modelo, filtro) de todo lo que pertenece a la empresa, en orden de borrado (hijos primero)."""
    emp_ids = [e for (e,) in db.query(Empleado.id).filter(Empleado.empresa_id == empresa.id)]
    per_ids = [p for (p,) in db.query(Periodo.id).filter(Periodo.empresa_id == empresa.id)]
    liq_ids = [l for (l,) in db.query(Liquidacion.id).filter(Liquidacion.periodo_id.in_(per_ids))] if per_ids else []
    return [
        ("liquidacion_detalle", LiquidacionDetalle, LiquidacionDetalle.liquidacion_id.in_(liq_ids) if liq_ids else None),
        ("liquidaciones", Liquidacion, Liquidacion.periodo_id.in_(per_ids) if per_ids else None),
        ("resultados", Resultado, Resultado.periodo_id.in_(per_ids) if per_ids else None),
        ("detalle", Detalle, Detalle.periodo_id.in_(per_ids) if per_ids else None),
        ("novedades", Novedad, Novedad.periodo_id.in_(per_ids) if per_ids else None),
        ("periodo_parametros", PeriodoParametro, PeriodoParametro.periodo_id.in_(per_ids) if per_ids else None),
        ("periodo_afp", PeriodoAFP, PeriodoAFP.periodo_id.in_(per_ids) if per_ids else None),
        ("periodos", Periodo, Periodo.empresa_id == empresa.id),
        ("cts_depositos", CtsDeposito, CtsDeposito.empresa_id == empresa.id),
        ("liquidaciones_manuales", LiquidacionManual, LiquidacionManual.empleado_id.in_(emp_ids) if emp_ids else None),
        ("vinculos_anteriores", Vinculo, Vinculo.empleado_id.in_(emp_ids) if emp_ids else None),
        ("rentas_previas_5ta", RentaPrevia5ta, RentaPrevia5ta.empleado_id.in_(emp_ids) if emp_ids else None),
        ("vacaciones", Vacacion, Vacacion.empleado_id.in_(emp_ids) if emp_ids else None),
        ("ausentismos", Ausentismo, Ausentismo.empleado_id.in_(emp_ids) if emp_ids else None),
        ("beneficios", Beneficio, Beneficio.empleado_id.in_(emp_ids) if emp_ids else None),
        ("sueldos", Sueldo, Sueldo.empleado_id.in_(emp_ids) if emp_ids else None),
        ("empleados", Empleado, Empleado.empresa_id == empresa.id),
        ("config_plame", ConfigPlame, ConfigPlame.empresa_id == empresa.id),
        ("config_cuentas", ConfigCuenta, ConfigCuenta.empresa_id == empresa.id),
        ("config_vigencias", ConfigVigencia, ConfigVigencia.empresa_id == empresa.id),
        ("cc_valores", CcValor, CcValor.empresa_id == empresa.id),
    ]


@router.get(API + "/empresas/{empresa_id}/exportar-datos")
def exportar_datos(empresa: Empresa = Depends(empresa_actual), acceso: Acceso = Depends(exigir_propietario), db: Session = Depends(get_db)):
    """Copia completa de los datos de la empresa (JSON). Solo el propietario de la cuenta."""
    datos = {"exportado_el": datetime.now().isoformat(timespec="seconds"), "empresa": _fila(empresa)}
    for nombre, modelo, filtro in reversed(_conjuntos(db, empresa)):
        datos[nombre] = [_fila(o) for o in db.query(modelo).filter(filtro)] if filtro is not None else []
    db.add(Auditoria(cuenta_id=empresa.cuenta_id, empresa_id=empresa.id, accion="EXPORTAR_DATOS", detalle=f"RUC {empresa.ruc}"))
    db.commit()
    return Response(json.dumps(datos, ensure_ascii=False, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename=datos_{empresa.ruc}.json'})


@router.delete(API + "/empresas/{empresa_id}")
def suprimir_empresa(confirmar_ruc: str = "", empresa: Empresa = Depends(empresa_actual), acceso: Acceso = Depends(exigir_propietario),
                     db: Session = Depends(get_db)):
    """Supresión definitiva de la empresa y todos sus datos. Exige escribir el RUC para confirmar."""
    if confirmar_ruc.strip() != empresa.ruc:
        raise HTTPException(422, "Para suprimir, escriba el RUC de la empresa en confirmar_ruc. Esta acción no se puede deshacer; descargue antes una copia.")
    contadores = {}
    for nombre, modelo, filtro in _conjuntos(db, empresa):
        if filtro is not None:
            contadores[nombre] = db.query(modelo).filter(filtro).delete(synchronize_session=False)
    ruc, cid, eid = empresa.ruc, empresa.cuenta_id, empresa.id
    db.delete(empresa)
    quien = acceso.usuario.email if acceso.usuario else ("administrador" if acceso.admin else "clave API")
    db.add(Auditoria(cuenta_id=cid, empresa_id=eid, accion="SUPRIMIR_EMPRESA", detalle=f"RUC {ruc} suprimida por {quien}"))
    db.commit()
    return {"ok": True, "ruc": ruc, "suprimidos": contadores}
