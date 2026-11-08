"""Planilla Ágil API v2.  Arranque:  uvicorn app.main:app --host 0.0.0.0 --port $PORT"""
from datetime import date
from decimal import Decimal
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import calculo, exportadores, reglas as R
from .config import allowed_origins
from .db import get_db
from .diccionario import ErrorDeNegocio
from .models import (Auditoria, Ausentismo, Concepto, Cuenta, Detalle, Diccionario, DiccionarioAFP, Empleado,
                     Empresa, Novedad, Periodo, RentaPrevia5ta, Resultado, Sueldo, TipoSuspension, Vacacion)
from .schemas import (AusentismoIn, BajaIn, EmpleadoIn, EmpleadoPatch, EmpresaIn, MONEY, NovedadFila, PeriodoIn, ReaperturaIn,
                      RentaPreviaIn, SueldoIn, VacacionIn)
from .security import empresa_actual, get_cuenta

app = FastAPI(title="Planilla Ágil API", version="2.0.0",
              description="Motor de nómina peruana (Régimen General) guiado por Diccionario versionado.")

# Solo orígenes explícitos; autenticación por cabecera (no cookies) => sin credenciales cruzadas.
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins(), allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(ErrorDeNegocio)
async def _error_negocio(_, exc: ErrorDeNegocio):
    return JSONResponse(status_code=exc.codigo, content={"detail": exc.mensaje})


API = "/api/v1"


def _periodo(db: Session, empresa: Empresa, periodo_id: int) -> Periodo:
    p = db.query(Periodo).filter(Periodo.id == periodo_id, Periodo.empresa_id == empresa.id).first()
    if not p:
        raise HTTPException(404, "Periodo no encontrado.")
    return p


def _empleado(db: Session, empresa: Empresa, empleado_id: int) -> Empleado:
    e = db.query(Empleado).filter(Empleado.id == empleado_id, Empleado.empresa_id == empresa.id).first()
    if not e:
        raise HTTPException(404, "Colaborador no encontrado.")
    return e


@app.get("/")
def raiz():
    return {"app": "Planilla Ágil API", "version": app.version}


@app.get(f"{API}/health")
def health():
    return {"status": "ok"}


# ------------------------------------------------------------------ empresas
@app.get(f"{API}/empresas")
def listar_empresas(cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    activos = lambda eid: db.query(Empleado).filter(Empleado.empresa_id == eid, Empleado.fecha_cese.is_(None)).count()
    return [{"id": e.id, "ruc": e.ruc, "razon_social": e.razon_social, "trabajadores_activos": activos(e.id),
             "limite_trabajadores": cuenta.max_trabajadores}
            for e in db.query(Empresa).filter(Empresa.cuenta_id == cuenta.id).order_by(Empresa.razon_social)]


@app.post(f"{API}/empresas", status_code=201)
def crear_empresa(data: EmpresaIn, cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    if db.query(Empresa).filter(Empresa.cuenta_id == cuenta.id).count() >= cuenta.max_empresas:
        raise HTTPException(403, f"Tu plan {cuenta.plan} permite {cuenta.max_empresas} empresa(s).")
    if db.query(Empresa).filter(Empresa.cuenta_id == cuenta.id, Empresa.ruc == data.ruc).first():
        raise HTTPException(409, "Ese RUC ya está registrado en tu cuenta.")
    dic = (db.query(Diccionario).filter(Diccionario.codigo == data.diccionario_codigo, Diccionario.estado == "ACTIVO")
           .order_by(Diccionario.version.desc()).first())
    if not dic:
        raise HTTPException(422, f"Diccionario {data.diccionario_codigo} no existe.")
    e = Empresa(cuenta_id=cuenta.id, ruc=data.ruc, razon_social=data.razon_social.upper(), diccionario_id=dic.id)
    db.add(e)
    db.commit()
    return {"id": e.id, "ruc": e.ruc, "razon_social": e.razon_social}


# ------------------------------------------------------------------ colaboradores
@app.get(API + "/empresas/{empresa_id}/empleados")
def listar_empleados(empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    hoy = date.today()
    return [{"id": e.id, "nombre": f"{e.apellidos}, {e.nombres}", "documento": e.documento_identidad,
             "puesto": e.puesto, "sede": e.sede, "regimen": e.regimen_codigo,
             "sistema_pensionario": e.sistema_pensionario, "afp": e.afp_nombre, "cuspp": e.cuspp,
             "banco": e.banco, "cuenta": e.numero_cuenta, "estado": e.estado,
             "asignacion_familiar": e.asignacion_familiar, "cobertura_eps": e.cobertura_eps,
             "fecha_ingreso": e.fecha_ingreso, "fecha_cese": e.fecha_cese,
             "sueldo_vigente": calculo.sueldo_a_fecha(db, e.id, hoy)}
            for e in db.query(Empleado).filter(Empleado.empresa_id == empresa.id)
            .order_by(Empleado.apellidos, Empleado.nombres)]


@app.post(API + "/empresas/{empresa_id}/empleados", status_code=201)
def crear_empleado(data: EmpleadoIn, empresa: Empresa = Depends(empresa_actual),
                   cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    activos = db.query(Empleado).filter(Empleado.empresa_id == empresa.id, Empleado.fecha_cese.is_(None)).count()
    if activos >= cuenta.max_trabajadores:
        raise HTTPException(403, f"Tu plan {cuenta.plan} permite hasta {cuenta.max_trabajadores} trabajadores por empresa.")
    if db.query(Empleado).filter(Empleado.empresa_id == empresa.id, Empleado.tipo_documento == data.tipo_documento,
                                 Empleado.documento_identidad == data.documento_identidad).first():
        raise HTTPException(409, "El documento ya está registrado en esta empresa.")
    if data.sistema_pensionario == "AFP":
        nombres = {a.nombre.upper() for a in db.query(DiccionarioAFP).filter(DiccionarioAFP.diccionario_id == empresa.diccionario_id)}
        if data.afp_nombre.upper() not in nombres:
            raise HTTPException(422, f"AFP no reconocida. Opciones: {', '.join(sorted(nombres))}")
    e = Empleado(empresa_id=empresa.id, nombres=data.nombres.upper(), apellidos=data.apellidos.upper(),
                 tipo_documento=data.tipo_documento, documento_identidad=data.documento_identidad,
                 puesto=(data.puesto or "").upper() or None, sede=data.sede, fecha_ingreso=data.fecha_ingreso,
                 sistema_pensionario=data.sistema_pensionario,
                 afp_nombre=data.afp_nombre.upper() if data.afp_nombre else None, cuspp=data.cuspp,
                 banco=data.banco, numero_cuenta=data.numero_cuenta, asignacion_familiar=data.asignacion_familiar,
                 cobertura_eps=data.cobertura_eps, regimen_codigo="RG_728")
    db.add(e)
    db.flush()
    db.add(Sueldo(empleado_id=e.id, monto=Decimal(str(data.sueldo_basico)), fecha_vigencia=data.fecha_ingreso,
                  motivo="Sueldo inicial de contrato"))
    db.commit()
    return {"id": e.id, "mensaje": f"{e.apellidos}, {e.nombres} registrado."}


@app.post(API + "/empresas/{empresa_id}/empleados/{empleado_id}/baja")
def dar_de_baja(empleado_id: int, data: BajaIn, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    if e.estado != "ACTIVO":
        raise HTTPException(409, "El colaborador ya está cesado.")
    if data.fecha_cese < e.fecha_ingreso:
        raise HTTPException(422, "La fecha de cese no puede ser anterior al ingreso.")
    e.fecha_cese, e.motivo_cese, e.estado = data.fecha_cese, data.motivo, "CESADO"
    db.commit()
    return {"estado": e.estado}


@app.post(API + "/empresas/{empresa_id}/empleados/{empleado_id}/reactivar-por-error")
def reactivar_por_error(empleado_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    if e.estado != "CESADO":
        raise HTTPException(409, "El colaborador no está cesado.")
    e.fecha_cese, e.motivo_cese, e.estado = None, None, "ACTIVO"
    db.commit()
    return {"estado": e.estado}


@app.get(API + "/empresas/{empresa_id}/empleados/{empleado_id}/sueldos")
def listar_sueldos(empleado_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    _empleado(db, empresa, empleado_id)
    filas = db.query(Sueldo).filter(Sueldo.empleado_id == empleado_id).order_by(Sueldo.fecha_vigencia.desc()).all()
    hoy = date.today()
    vigente = next((s.id for s in filas if s.fecha_vigencia <= hoy), None)
    return [{"id": s.id, "monto": s.monto, "fecha_vigencia": s.fecha_vigencia, "motivo": s.motivo,
             "vigente": s.id == vigente} for s in filas]


@app.post(API + "/empresas/{empresa_id}/empleados/{empleado_id}/sueldos", status_code=201)
def registrar_sueldo(empleado_id: int, data: SueldoIn, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    if data.fecha_vigencia < e.fecha_ingreso:
        raise HTTPException(422, "La vigencia no puede ser anterior al ingreso.")
    if db.query(Sueldo).filter(Sueldo.empleado_id == e.id, Sueldo.fecha_vigencia == data.fecha_vigencia).first():
        raise HTTPException(409, "Ya existe un sueldo con esa fecha de vigencia.")
    db.add(Sueldo(empleado_id=e.id, monto=Decimal(str(data.monto)), fecha_vigencia=data.fecha_vigencia, motivo=data.motivo))
    db.commit()
    return {"ok": True}


@app.post(API + "/empresas/{empresa_id}/empleados/{empleado_id}/ausentismos", status_code=201)
def registrar_ausentismo(empleado_id: int, data: AusentismoIn, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    ts = db.query(TipoSuspension).filter(TipoSuspension.diccionario_id == empresa.diccionario_id,
                                         TipoSuspension.codigo_plame == data.codigo_plame).first()
    if not ts:
        raise HTTPException(422, f"Código de suspensión {data.codigo_plame} no existe en el Diccionario.")
    if db.query(Ausentismo).filter(Ausentismo.empleado_id == e.id, Ausentismo.estado == "REGISTRADO",
                                   Ausentismo.fecha_inicio <= data.fecha_fin, Ausentismo.fecha_fin >= data.fecha_inicio).first():
        raise HTTPException(409, "Las fechas se cruzan con otro ausentismo registrado.")
    db.add(Ausentismo(empleado_id=e.id, tipo_suspension_id=ts.id, fecha_inicio=data.fecha_inicio, fecha_fin=data.fecha_fin))
    db.commit()
    return {"ok": True, "concepto": ts.concepto_codigo, "dias": (data.fecha_fin - data.fecha_inicio).days + 1}


@app.post(API + "/empresas/{empresa_id}/empleados/{empleado_id}/vacaciones", status_code=201)
def registrar_vacacion(empleado_id: int, data: VacacionIn, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    if db.query(Vacacion).filter(Vacacion.empleado_id == e.id, Vacacion.estado == "REGISTRADO",
                                 Vacacion.fecha_inicio <= data.fecha_fin, Vacacion.fecha_fin >= data.fecha_inicio).first():
        raise HTTPException(409, "Las fechas se cruzan con otras vacaciones.")
    db.add(Vacacion(empleado_id=e.id, fecha_inicio=data.fecha_inicio, fecha_fin=data.fecha_fin, dias_vendidos=data.dias_vendidos))
    db.commit()
    return {"ok": True}


@app.post(API + "/empresas/{empresa_id}/empleados/{empleado_id}/rentas-previas-5ta", status_code=201)
def registrar_renta_previa(empleado_id: int, data: RentaPreviaIn, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    db.add(RentaPrevia5ta(empleado_id=e.id, anio=data.anio, mes=data.mes, ruc=data.ruc, razon_social=data.razon_social.upper(),
                          ingreso_afecto=Decimal(str(data.ingreso_afecto)), retencion=Decimal(str(data.retencion))))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ periodos y cálculo
@app.get(API + "/empresas/{empresa_id}/periodos")
def listar_periodos(empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    return [{"id": p.id, "anio": p.anio, "mes": p.mes, "fase": p.fase, "estado": p.estado}
            for p in db.query(Periodo).filter(Periodo.empresa_id == empresa.id).order_by(Periodo.anio.desc(), Periodo.mes.desc())]


@app.post(API + "/empresas/{empresa_id}/periodos", status_code=201)
def crear_periodo(data: PeriodoIn, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    if db.query(Periodo).filter(Periodo.empresa_id == empresa.id, Periodo.anio == data.anio, Periodo.mes == data.mes,
                                Periodo.fase == data.fase).first():
        raise HTTPException(409, "Ese periodo ya existe.")
    p = Periodo(empresa_id=empresa.id, anio=data.anio, mes=data.mes, fase=data.fase,
                fecha_inicio=date(data.anio, data.mes, 1), fecha_fin=R.fin_de_mes(data.anio, data.mes),
                diccionario_id=empresa.diccionario_id)
    db.add(p)
    db.commit()
    return {"id": p.id, "estado": p.estado}


@app.put(API + "/empresas/{empresa_id}/periodos/{periodo_id}/novedades")
def cargar_novedades(periodo_id: int, filas: List[NovedadFila], empresa: Empresa = Depends(empresa_actual),
                     db: Session = Depends(get_db)):
    """Carga masiva con Pre-Check: DNI inexistente, código inválido o más de 2 decimales.
    Todo o nada: si hay un solo error no se guarda nada y se devuelve la lista de errores."""
    p = _periodo(db, empresa, periodo_id)
    calculo.exigir_abierto(p)
    conceptos = {c.codigo: c for c in db.query(Concepto).filter(Concepto.diccionario_id == p.diccionario_id)}
    docs = {e.documento_identidad: e.id for e in db.query(Empleado).filter(Empleado.empresa_id == empresa.id)}
    errores, validas = [], []
    for i, f in enumerate(filas, start=1):
        c = conceptos.get(f.codigo)
        if f.documento not in docs:
            errores.append({"fila": i, "error": "DNI inexistente"})
        elif c is None:
            errores.append({"fila": i, "error": "Código de concepto inválido"})
        elif c.calculado:
            errores.append({"fila": i, "error": "Concepto calculado por el motor; no admite carga"})
        elif not MONEY.match(f.valor.strip()):
            errores.append({"fila": i, "error": "Valor no numérico o con más de 2 decimales"})
        else:
            validas.append((docs[f.documento], f.codigo, Decimal(f.valor.strip())))
    if errores:
        return JSONResponse(status_code=422, content={"detail": "La carga tiene errores; no se guardó nada.", "errores": errores})
    for emp_id, codigo, valor in validas:
        n = db.query(Novedad).filter(Novedad.periodo_id == p.id, Novedad.empleado_id == emp_id,
                                     Novedad.concepto_codigo == codigo).first()
        if n:
            n.valor = valor
        else:
            db.add(Novedad(periodo_id=p.id, empleado_id=emp_id, concepto_codigo=codigo, valor=valor))
    db.commit()
    return {"guardadas": len(validas)}


@app.post(API + "/empresas/{empresa_id}/planillas/{periodo_id}/recalcular")
def recalcular(periodo_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    p = _periodo(db, empresa, periodo_id)
    try:
        r = calculo.recalcular_periodo(db, p)
    except Exception:
        db.rollback()
        raise
    return {"status": "success", "periodo": f"{p.mes:02d}/{p.anio}", **r}


@app.post(API + "/empresas/{empresa_id}/planillas/{periodo_id}/cerrar")
def cerrar(periodo_id: int, empresa: Empresa = Depends(empresa_actual), cuenta: Cuenta = Depends(get_cuenta),
           db: Session = Depends(get_db)):
    calculo.cerrar_periodo(db, _periodo(db, empresa, periodo_id), cuenta.id)
    return {"estado": "CERRADO"}


@app.post(API + "/empresas/{empresa_id}/planillas/{periodo_id}/reabrir")
def reabrir(periodo_id: int, data: ReaperturaIn, empresa: Empresa = Depends(empresa_actual),
            cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    calculo.reabrir_periodo(db, _periodo(db, empresa, periodo_id), cuenta.id, data.motivo)
    return {"estado": "ABIERTO"}


@app.get(API + "/empresas/{empresa_id}/planillas/{periodo_id}/resultados")
def resultados(periodo_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    p = _periodo(db, empresa, periodo_id)
    return {"estado": p.estado, "filas": [
        {"empleado_id": r.empleado_id, "colaborador": (r.snapshot or {}).get("colaborador"),
         "documento": (r.snapshot or {}).get("documento"), "ingresos": r.remuneracion_computable,
         "descuentos": r.total_descuentos, "aportes_empleador": r.aportes_empleador, "neto": r.neto_a_pagar,
         "dias_trabajados": r.dias_trabajados}
        for r in db.query(Resultado).filter(Resultado.periodo_id == p.id).order_by(Resultado.id)]}


@app.get(API + "/empresas/{empresa_id}/planillas/{periodo_id}/resultados/{empleado_id}/detalle")
def detalle(periodo_id: int, empleado_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """'¿Por qué este neto?': cada concepto con su fórmula."""
    p = _periodo(db, empresa, periodo_id)
    r = db.query(Resultado).filter(Resultado.periodo_id == p.id, Resultado.empleado_id == empleado_id).first()
    if not r:
        raise HTTPException(404, "Sin resultados para ese colaborador.")
    return {"snapshot": r.snapshot, "neto": r.neto_a_pagar,
            "conceptos": [{"codigo": d.concepto_codigo, "categoria": d.categoria, "monto": d.monto,
                           "origen": d.origen, "formula": d.formula}
                          for d in db.query(Detalle).filter(Detalle.periodo_id == p.id, Detalle.empleado_id == empleado_id)
                          .order_by(Detalle.id)]}



# ------------------------------------------------------------------ consultas que usa el frontend
@app.patch(API + "/empresas/{empresa_id}/empleados/{empleado_id}")
def actualizar_empleado(empleado_id: int, data: EmpleadoPatch, empresa: Empresa = Depends(empresa_actual),
                        db: Session = Depends(get_db)):
    """Edición parcial del legajo. Los periodos cerrados no cambian: guardan su propio snapshot."""
    e = _empleado(db, empresa, empleado_id)
    cambios = data.model_dump(exclude_unset=True)
    for campo in ("afp_nombre",):
        if cambios.get(campo):
            cambios[campo] = cambios[campo].upper()
    for campo, valor in cambios.items():
        setattr(e, campo, valor)
    if e.sistema_pensionario == "AFP":
        nombres = {a.nombre.upper() for a in db.query(DiccionarioAFP).filter(DiccionarioAFP.diccionario_id == empresa.diccionario_id)}
        if (e.afp_nombre or "") not in nombres:
            db.rollback()
            raise HTTPException(422, f"AFP no reconocida. Opciones: {', '.join(sorted(nombres))}")
    else:
        e.afp_nombre = None
    db.commit()
    return {"ok": True}


@app.get(API + "/empresas/{empresa_id}/empleados/{empleado_id}/ausentismos")
def listar_ausentismos(empleado_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    _empleado(db, empresa, empleado_id)
    q = (db.query(Ausentismo, TipoSuspension).join(TipoSuspension, TipoSuspension.id == Ausentismo.tipo_suspension_id)
         .filter(Ausentismo.empleado_id == empleado_id).order_by(Ausentismo.fecha_inicio.desc()))
    return [{"id": a.id, "codigo_plame": t.codigo_plame, "descripcion": t.descripcion, "tipo": t.tipo,
             "fecha_inicio": a.fecha_inicio, "fecha_fin": a.fecha_fin,
             "dias": (a.fecha_fin - a.fecha_inicio).days + 1, "concepto": t.concepto_codigo, "estado": a.estado}
            for a, t in q]


@app.get(API + "/empresas/{empresa_id}/empleados/{empleado_id}/vacaciones")
def listar_vacaciones(empleado_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    _empleado(db, empresa, empleado_id)
    return [{"id": v.id, "fecha_inicio": v.fecha_inicio, "fecha_fin": v.fecha_fin,
             "dias_gozados": (v.fecha_fin - v.fecha_inicio).days + 1, "dias_vendidos": v.dias_vendidos, "estado": v.estado}
            for v in db.query(Vacacion).filter(Vacacion.empleado_id == empleado_id).order_by(Vacacion.fecha_inicio.desc())]


@app.get(API + "/empresas/{empresa_id}/diccionario/conceptos")
def conceptos_cargables(empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """Conceptos que el usuario puede cargar como novedad (los demás los calcula el motor)."""
    return [{"codigo": c.codigo, "descripcion": c.descripcion, "categoria": c.categoria}
            for c in db.query(Concepto).filter(Concepto.diccionario_id == empresa.diccionario_id, Concepto.calculado.is_(False))
            .order_by(Concepto.categoria, Concepto.codigo)]


@app.get(API + "/empresas/{empresa_id}/periodos/{periodo_id}/novedades")
def listar_novedades(periodo_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    p = _periodo(db, empresa, periodo_id)
    desc = {c.codigo: c.descripcion for c in db.query(Concepto).filter(Concepto.diccionario_id == p.diccionario_id)}
    q = (db.query(Novedad, Empleado).join(Empleado, Empleado.id == Novedad.empleado_id)
         .filter(Novedad.periodo_id == p.id).order_by(Empleado.apellidos, Novedad.concepto_codigo))
    return [{"id": n.id, "empleado_id": e.id, "colaborador": f"{e.apellidos}, {e.nombres}", "documento": e.documento_identidad,
             "codigo": n.concepto_codigo, "descripcion": desc.get(n.concepto_codigo, ""), "valor": n.valor} for n, e in q]


@app.delete(API + "/empresas/{empresa_id}/periodos/{periodo_id}/novedades/{novedad_id}")
def borrar_novedad(periodo_id: int, novedad_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    p = _periodo(db, empresa, periodo_id)
    calculo.exigir_abierto(p)
    n = db.query(Novedad).filter(Novedad.id == novedad_id, Novedad.periodo_id == p.id).first()
    if not n:
        raise HTTPException(404, "Novedad no encontrada.")
    db.delete(n)
    db.commit()
    return {"ok": True}


@app.get(API + "/empresas/{empresa_id}/planillas/{periodo_id}/resumen-conceptos")
def resumen_conceptos(periodo_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """Resumen consolidado vertical por concepto (@), agrupado por categoría."""
    p = _periodo(db, empresa, periodo_id)
    desc = {c.codigo: c.descripcion for c in db.query(Concepto).filter(Concepto.diccionario_id == p.diccionario_id)}
    filas = (db.query(Detalle.concepto_codigo, Detalle.categoria, func.sum(Detalle.monto), func.count(Detalle.empleado_id))
             .filter(Detalle.periodo_id == p.id).group_by(Detalle.concepto_codigo, Detalle.categoria)
             .order_by(Detalle.categoria, Detalle.concepto_codigo).all())
    neto = db.query(func.coalesce(func.sum(Resultado.neto_a_pagar), 0)).filter(Resultado.periodo_id == p.id).scalar()
    return {"neto_total": neto, "conceptos": [{"codigo": c, "descripcion": desc.get(c, ""), "categoria": cat,
                                               "total": t, "colaboradores": n} for c, cat, t, n in filas]}



@app.get(API + "/empresas/{empresa_id}/diccionario/parametros")
def parametros_vigentes(empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """Parámetros del Diccionario vigentes hoy (solo lectura: se cambian publicando una nueva versión)."""
    from .diccionario import cargar_parametros
    dic = db.get(Diccionario, empresa.diccionario_id)
    pr = cargar_parametros(db, empresa.diccionario_id, date.today())
    return {"diccionario": f"{dic.codigo} v{dic.version}", "parametros": {k: str(v) for k, v in pr.p.items()},
            "afps": [{"nombre": a.nombre, "aporte": a.aporte, "comision": a.comision, "prima": a.prima, "tope_prima": a.tope_prima}
                     for a in sorted(pr.afps.values(), key=lambda x: x.nombre)]}


# ------------------------------------------------------------------ exportaciones
@app.get(API + "/empresas/{empresa_id}/planillas/{periodo_id}/exportar-afpnet")
def exportar_afpnet(periodo_id: int, formato: str = Query("xlsx", pattern="^(xlsx|csv)$"),
                    empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    p = _periodo(db, empresa, periodo_id)
    contenido, mime = exportadores.afpnet_bytes(exportadores.afpnet_filas(db, empresa, p), formato)
    nombre = f"AFPNET_{empresa.ruc}_{p.anio}_{p.mes:02d}.{formato}"
    return Response(contenido, media_type=mime, headers={"Content-Disposition": f"attachment; filename={nombre}"})


@app.get(API + "/empresas/{empresa_id}/planillas/{periodo_id}/exportar-plame-zip")
def exportar_plame(periodo_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    p = _periodo(db, empresa, periodo_id)
    contenido, prefijo = exportadores.plame_zip(db, empresa, p)
    return Response(contenido, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=PLAME_{prefijo}.zip"})
