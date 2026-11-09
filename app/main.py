"""Planilla Ágil API v2.  Arranque:  uvicorn app.main:app --host 0.0.0.0 --port $PORT"""
import html
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import calculo, exportadores, liquidacion as L, parametros_periodo, pdf_boleta, pdf_liquidacion, reglas as R, semestrales
from .config import allowed_origins
from .db import get_db
from .diccionario import ErrorDeNegocio
from .models import (Auditoria, Ausentismo, Concepto, Cuenta, Detalle, Diccionario, DiccionarioAFP, Empleado,
                     Empresa, Novedad, Periodo, RentaPrevia5ta, Resultado, Sueldo, TipoSuspension, Vacacion)
from .models import Liquidacion, LiquidacionDetalle, Vinculo
from .schemas import (EmpresaPatch, ParametrosPeriodoIn, LiquidacionCerrarIn, CuentaIn, CuentaPatch, CtsCalcularIn, CtsDepositarIn, CtsRevertirIn, ReingresoIn, AusentismoIn, BajaIn, EmpleadoIn, EmpleadoPatch, EmpresaIn, MONEY, NovedadFila, PeriodoIn, ReaperturaIn,
                      RentaPreviaIn, SueldoIn, VacacionIn)
from .security import Acceso, empresa_actual, exigir_admin, generar_api_key, get_acceso, get_cuenta, hash_key

@asynccontextmanager
async def _ciclo_de_vida(_):
    if os.getenv("AUTO_INIT", "1") != "0":
        from .arranque import preparar_base
        preparar_base()          # crea tablas, siembra el Diccionario y activa el bloqueo de periodos cerrados
    yield


app = FastAPI(title="Planilla Ágil API", version="2.0.0", lifespan=_ciclo_de_vida,
              description="Motor de nómina peruana (Régimen General) guiado por Diccionario versionado.")

# Solo orígenes explícitos; autenticación por cabecera (no cookies) => sin credenciales cruzadas.
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins(), allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Content-Disposition", "X-Avisos"])


@app.exception_handler(ErrorDeNegocio)
async def _error_negocio(_, exc: ErrorDeNegocio):
    return JSONResponse(status_code=exc.codigo, content={"detail": exc.mensaje})


API = "/api/v1"


def _avisos_header(av: dict, sin_codigo=()) -> str:
    """Resumen en ASCII para que el navegador avise (cabecera X-Avisos)."""
    partes = [f"liquidaciones-incluidas={av['incluidas']}"]
    if av["abiertas"]:
        partes.append(f"liquidaciones-sin-cerrar={len(av['abiertas'])}")
    if sin_codigo:
        partes.append("conceptos-sin-codigo-plame=" + ",".join(sin_codigo))
    return "; ".join(partes).encode("ascii", "ignore").decode()

# ------------------------------------------------------------------ página de alta inicial (sin instalar nada)
_PAGINA = """<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Planilla Ágil · Crear cuenta</title><body style="font-family:system-ui;background:#0B0F19;color:#e2e8f0;display:flex;justify-content:center;padding:32px">
<div style="max-width:420px;width:100%;background:#131926;border:1px solid #1E293B;border-radius:20px;padding:28px">{contenido}</div></body></html>"""


def _setup_activo() -> str:
    token = os.getenv("SETUP_TOKEN", "")
    if not token:
        raise HTTPException(404, "No disponible.")
    return token


@app.get("/setup", response_class=HTMLResponse, include_in_schema=False)
def pagina_setup():
    _setup_activo()
    return _PAGINA.format(contenido="""<h2 style="margin-top:0">Crear cuenta</h2>
      <form method="post" action="/setup" style="display:grid;gap:12px">
      <label>Código de instalación (SETUP_TOKEN)<input name="token" type="password" required style="width:100%;padding:10px;border-radius:10px;border:1px solid #334155;background:#0B0E17;color:#fff"></label>
      <label>Nombre de la cuenta<input name="nombre" required style="width:100%;padding:10px;border-radius:10px;border:1px solid #334155;background:#0B0E17;color:#fff"></label>
      <label>Plan<select name="plan" style="width:100%;padding:10px;border-radius:10px;border:1px solid #334155;background:#0B0E17;color:#fff"><option>GRATIS</option><option>FREELANCER</option></select></label>
      <button style="padding:12px;border:0;border-radius:12px;background:#5B46F6;color:#fff;font-weight:700">Crear cuenta y mostrar mi clave</button></form>""")


@app.post("/setup", response_class=HTMLResponse, include_in_schema=False)
def crear_cuenta_inicial(token: str = Form(...), nombre: str = Form(...), plan: str = Form("GRATIS"),
                         db: Session = Depends(get_db)):
    esperado = _setup_activo()
    if not secrets.compare_digest(token.encode(), esperado.encode()):
        raise HTTPException(403, "Código de instalación incorrecto.")
    maximos = {"GRATIS": (1, 10), "FREELANCER": (20, 10)}
    me, mt = maximos.get(plan, maximos["GRATIS"])
    clave = generar_api_key()
    db.add(Cuenta(nombre=nombre.strip()[:150], plan=plan if plan in maximos else "GRATIS", max_empresas=me,
                  max_trabajadores=mt, api_key_hash=hash_key(clave)))
    db.commit()
    return _PAGINA.format(contenido=f"""<h2 style="margin-top:0">Cuenta creada ✓</h2>
      <p>Copie esta clave y guárdela ahora. <b>No se vuelve a mostrar.</b></p>
      <p style="word-break:break-all;background:#0B0E17;border:1px solid #334155;border-radius:12px;padding:14px;font-family:Consolas,monospace;font-size:15px">{html.escape(clave)}</p>
      <p style="color:#94a3b8">Péguela en el HTML: engranaje → Clave de API.</p>""")



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
def listar_empresas(acceso: Acceso = Depends(get_acceso), db: Session = Depends(get_db)):
    """Las empresas de la cuenta; para el administrador, todas las empresas de todas las cuentas."""
    q = db.query(Empresa)
    if not acceso.admin:
        q = q.filter(Empresa.cuenta_id == acceso.cuenta.id)
    cuentas = {c.id: c for c in db.query(Cuenta)}
    activos = lambda eid: db.query(Empleado).filter(Empleado.empresa_id == eid, Empleado.fecha_cese.is_(None)).count()
    return [{"id": e.id, "ruc": e.ruc, "razon_social": e.razon_social, "trabajadores_activos": activos(e.id),
             "direccion_fiscal": e.direccion_fiscal, "representante_legal": e.representante_legal,
             "cargo_representante": e.cargo_representante,
             "limite_trabajadores": cuentas[e.cuenta_id].max_trabajadores, "cuenta_id": e.cuenta_id,
             "cuenta": cuentas[e.cuenta_id].nombre if acceso.admin else None}
            for e in q.order_by(Empresa.razon_social)]


@app.post(f"{API}/empresas", status_code=201)
def crear_empresa(data: EmpresaIn, acceso: Acceso = Depends(get_acceso), db: Session = Depends(get_db)):
    if acceso.admin:
        if not data.cuenta_id:
            raise HTTPException(422, "Como administrador, elija la cuenta dueña de la empresa.")
        cuenta = db.get(Cuenta, data.cuenta_id)
        if not cuenta:
            raise HTTPException(404, "La cuenta indicada no existe.")
    else:
        cuenta = acceso.cuenta
    if db.query(Empresa).filter(Empresa.cuenta_id == cuenta.id).count() >= cuenta.max_empresas:
        raise HTTPException(403, f"El plan {cuenta.plan} de esa cuenta permite {cuenta.max_empresas} empresa(s).")
    if db.query(Empresa).filter(Empresa.cuenta_id == cuenta.id, Empresa.ruc == data.ruc).first():
        raise HTTPException(409, "Ese RUC ya está registrado en la cuenta.")
    dic = (db.query(Diccionario).filter(Diccionario.codigo == data.diccionario_codigo, Diccionario.estado == "ACTIVO")
           .order_by(Diccionario.version.desc()).first())
    if not dic:
        raise HTTPException(422, f"Diccionario {data.diccionario_codigo} no existe.")
    e = Empresa(cuenta_id=cuenta.id, ruc=data.ruc, razon_social=data.razon_social.upper(), diccionario_id=dic.id,
                direccion_fiscal=data.direccion_fiscal, representante_legal=data.representante_legal,
                cargo_representante=data.cargo_representante)
    db.add(e)
    if acceso.admin:
        db.add(Auditoria(cuenta_id=cuenta.id, accion="ADMIN_CREA_EMPRESA", detalle=f"RUC {data.ruc}"))
    db.commit()
    return {"id": e.id, "ruc": e.ruc, "razon_social": e.razon_social, "cuenta_id": cuenta.id}


@app.patch(API + "/empresas/{empresa_id}")
def actualizar_empresa(data: EmpresaPatch, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """Datos de la empresa que salen en boletas y liquidaciones (razón social, dirección, representante)."""
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(empresa, campo, valor.upper() if campo == "razon_social" and valor else valor)
    db.commit()
    return {"ok": True, "razon_social": empresa.razon_social}


# ------------------------------------------------------------------ administrador del software
@app.get(API + "/admin/cuentas")
def admin_listar_cuentas(_: Acceso = Depends(exigir_admin), db: Session = Depends(get_db)):
    contar = lambda cid: db.query(Empresa).filter(Empresa.cuenta_id == cid).count()
    return [{"id": c.id, "nombre": c.nombre, "plan": c.plan, "max_empresas": c.max_empresas,
             "max_trabajadores": c.max_trabajadores, "activa": c.activa, "empresas": contar(c.id)}
            for c in db.query(Cuenta).order_by(Cuenta.nombre)]


@app.post(API + "/admin/cuentas", status_code=201)
def admin_crear_cuenta(data: CuentaIn, _: Acceso = Depends(exigir_admin), db: Session = Depends(get_db)):
    """Crea una cuenta y devuelve su clave UNA sola vez. Reemplaza a la página /setup."""
    maximos = {"GRATIS": (1, 10), "FREELANCER": (20, 10)}
    me, mt = maximos[data.plan]
    clave = generar_api_key()
    c = Cuenta(nombre=data.nombre.strip(), plan=data.plan, max_empresas=me, max_trabajadores=mt, api_key_hash=hash_key(clave))
    db.add(c)
    db.add(Auditoria(accion="ADMIN_CREA_CUENTA", detalle=data.nombre.strip()))
    db.commit()
    return {"id": c.id, "nombre": c.nombre, "plan": c.plan, "api_key": clave,
            "aviso": "Guarde esta clave ahora: no se vuelve a mostrar."}


@app.patch(API + "/admin/cuentas/{cuenta_id}")
def admin_editar_cuenta(cuenta_id: int, data: CuentaPatch, _: Acceso = Depends(exigir_admin), db: Session = Depends(get_db)):
    c = db.get(Cuenta, cuenta_id)
    if not c:
        raise HTTPException(404, "Cuenta no encontrada.")
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(c, campo, valor)
    db.add(Auditoria(cuenta_id=c.id, accion="ADMIN_EDITA_CUENTA", detalle=str(data.model_dump(exclude_unset=True))))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ colaboradores
@app.get(API + "/empresas/{empresa_id}/empleados")
def listar_empleados(empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    hoy = date.today()
    return [{"id": e.id, "nombre": f"{e.apellidos}, {e.nombres}", "documento": e.documento_identidad,
             "puesto": e.puesto, "sede": e.sede, "regimen": e.regimen_codigo,
             "sistema_pensionario": e.sistema_pensionario, "afp": e.afp_nombre, "cuspp": e.cuspp,
             "banco": e.banco, "cuenta": e.numero_cuenta, "estado": e.estado,
             "asignacion_familiar": e.asignacion_familiar, "cobertura_eps": e.cobertura_eps,
             "fecha_ingreso": e.fecha_ingreso, "fecha_cese": e.fecha_cese, "motivo_cese": e.motivo_cese,
             "clase_despido": e.clase_despido, "tipo_contrato": e.tipo_contrato,
             "fecha_fin_contrato": e.fecha_fin_contrato, "area": e.area, "centro_costo": e.centro_costo,
             "categoria": e.categoria, "calificacion": e.calificacion, "tipo_comision": e.tipo_comision,
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
                 cobertura_eps=data.cobertura_eps, regimen_codigo="RG_728", tipo_contrato=data.tipo_contrato,
                 fecha_fin_contrato=data.fecha_fin_contrato, area=data.area, centro_costo=data.centro_costo,
                 categoria=data.categoria or "EMPLEADOS", calificacion=data.calificacion,
                 tipo_comision=data.tipo_comision if data.sistema_pensionario == "AFP" else "FLUJO")
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
    if data.motivo == L.MOTIVO_DESPIDO and data.clase_despido is None:
        raise HTTPException(422, "Con el motivo «02 Despido» indique la clase de despido (justificado, arbitrario o nulo).")
    e.fecha_cese, e.motivo_cese, e.estado = data.fecha_cese, data.motivo, "CESADO"
    e.clase_despido = data.clase_despido if data.motivo == L.MOTIVO_DESPIDO else None
    db.commit()
    return {"estado": e.estado}


@app.post(API + "/empresas/{empresa_id}/empleados/{empleado_id}/reactivar-por-error")
def reactivar_por_error(empleado_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    if e.estado != "CESADO":
        raise HTTPException(409, "El colaborador no está cesado.")
    L.exigir_sin_liquidacion_cerrada(db, e.id, "reactivarlo")      # solo una liquidación CERRADA bloquea; no hay borradores guardados
    e.fecha_cese, e.motivo_cese, e.clase_despido, e.estado = None, None, None, "ACTIVO"
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
    L.exigir_sin_liquidacion_cerrada(db, e.id, "registrar sueldos")
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
    L.exigir_sin_liquidacion_cerrada(db, e.id, "registrar ausentismos")
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
    L.exigir_sin_liquidacion_cerrada(db, e.id, "registrar vacaciones")
    if db.query(Vacacion).filter(Vacacion.empleado_id == e.id, Vacacion.estado == "REGISTRADO",
                                 Vacacion.fecha_inicio <= data.fecha_fin, Vacacion.fecha_fin >= data.fecha_inicio).first():
        raise HTTPException(409, "Las fechas se cruzan con otras vacaciones.")
    db.add(Vacacion(empleado_id=e.id, fecha_inicio=data.fecha_inicio, fecha_fin=data.fecha_fin, dias_vendidos=data.dias_vendidos))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ rentas previas de 5ta categoría (empleadores anteriores)
def _renta_dict(r: RentaPrevia5ta) -> dict:
    return {"id": r.id, "anio": r.anio, "mes": r.mes, "ruc": r.ruc, "razon_social": r.razon_social,
            "ingreso_afecto": r.ingreso_afecto, "retencion": r.retencion,
            "estado": f"INCORPORADO A LA PROYECCIÓN desde {r.mes:02d}/{r.anio}"}


def _renta(db: Session, e: Empleado, renta_id: int) -> RentaPrevia5ta:
    r = db.query(RentaPrevia5ta).filter(RentaPrevia5ta.id == renta_id, RentaPrevia5ta.empleado_id == e.id).first()
    if not r:
        raise HTTPException(404, "Renta previa no encontrada.")
    return r


def _exigir_sin_periodos_cerrados(db: Session, empresa: Empresa, anio: int, mes: int):
    """Una renta previa ya usada en un periodo CERRADO no se cambia: ese periodo quedó con ella incorporada."""
    cerrado = db.query(Periodo).filter(Periodo.empresa_id == empresa.id, Periodo.estado == "CERRADO", Periodo.anio == anio,
                                       Periodo.mes >= mes).first()
    if cerrado:
        raise HTTPException(403, f"El periodo {cerrado.mes:02d}/{cerrado.anio} está CERRADO y ya incorporó esta renta: "
                                 "reábralo (con motivo) antes de modificarla o eliminarla.")


@app.get(API + "/empresas/{empresa_id}/empleados/{empleado_id}/rentas-previas-5ta")
def listar_rentas_previas(empleado_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """Historial de rentas y retenciones de empleadores anteriores, con sus totales."""
    e = _empleado(db, empresa, empleado_id)
    filas = (db.query(RentaPrevia5ta).filter(RentaPrevia5ta.empleado_id == e.id)
             .order_by(RentaPrevia5ta.anio.desc(), RentaPrevia5ta.mes.desc(), RentaPrevia5ta.id.desc()).all())
    return {"filas": [_renta_dict(r) for r in filas],
            "total_ingreso": sum((Decimal(str(r.ingreso_afecto)) for r in filas), Decimal(0)),
            "total_retencion": sum((Decimal(str(r.retencion)) for r in filas), Decimal(0))}


@app.post(API + "/empresas/{empresa_id}/empleados/{empleado_id}/rentas-previas-5ta", status_code=201)
def registrar_renta_previa(empleado_id: int, data: RentaPreviaIn, empresa: Empresa = Depends(empresa_actual),
                           cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    L.exigir_sin_liquidacion_cerrada(db, e.id, "registrar rentas previas")
    ing, ret = Decimal(str(data.ingreso_afecto)), Decimal(str(data.retencion))
    if db.query(RentaPrevia5ta).filter(RentaPrevia5ta.empleado_id == e.id, RentaPrevia5ta.anio == data.anio, RentaPrevia5ta.mes == data.mes,
                                       RentaPrevia5ta.ruc == data.ruc, RentaPrevia5ta.ingreso_afecto == ing,
                                       RentaPrevia5ta.retencion == ret).first():
        raise HTTPException(409, "Esa renta previa ya está registrada (mismo periodo, RUC y montos).")
    r = RentaPrevia5ta(empleado_id=e.id, anio=data.anio, mes=data.mes, ruc=data.ruc, razon_social=data.razon_social.strip().upper(),
                       ingreso_afecto=ing, retencion=ret)
    db.add(r)
    db.add(Auditoria(cuenta_id=cuenta.id, empresa_id=empresa.id, accion="RENTA_PREVIA_REGISTRA", detalle=f"empleado {e.id} {data.mes:02d}/{data.anio} RUC {data.ruc}"))
    db.commit()
    return {"ok": True, **_renta_dict(r)}


@app.put(API + "/empresas/{empresa_id}/empleados/{empleado_id}/rentas-previas-5ta/{renta_id}")
def editar_renta_previa(empleado_id: int, renta_id: int, data: RentaPreviaIn, empresa: Empresa = Depends(empresa_actual),
                        cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    L.exigir_sin_liquidacion_cerrada(db, e.id, "modificar rentas previas")
    r = _renta(db, e, renta_id)
    _exigir_sin_periodos_cerrados(db, empresa, r.anio, r.mes)
    _exigir_sin_periodos_cerrados(db, empresa, data.anio, data.mes)
    r.anio, r.mes, r.ruc, r.razon_social = data.anio, data.mes, data.ruc, data.razon_social.strip().upper()
    r.ingreso_afecto, r.retencion = Decimal(str(data.ingreso_afecto)), Decimal(str(data.retencion))
    db.add(Auditoria(cuenta_id=cuenta.id, empresa_id=empresa.id, accion="RENTA_PREVIA_EDITA", detalle=f"empleado {e.id} renta {r.id}"))
    db.commit()
    return {"ok": True, **_renta_dict(r)}


@app.delete(API + "/empresas/{empresa_id}/empleados/{empleado_id}/rentas-previas-5ta/{renta_id}")
def eliminar_renta_previa(empleado_id: int, renta_id: int, empresa: Empresa = Depends(empresa_actual),
                          cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    L.exigir_sin_liquidacion_cerrada(db, e.id, "eliminar rentas previas")
    r = _renta(db, e, renta_id)
    _exigir_sin_periodos_cerrados(db, empresa, r.anio, r.mes)
    db.add(Auditoria(cuenta_id=cuenta.id, empresa_id=empresa.id, accion="RENTA_PREVIA_ELIMINA",
                     detalle=f"empleado {e.id} {r.mes:02d}/{r.anio} RUC {r.ruc} ingreso {r.ingreso_afecto} retención {r.retencion}"))
    db.delete(r)
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
    arrastrados = parametros_periodo.copiar_del_anterior(db, p)       # «viaja la información de un mes al otro»
    return {"id": p.id, "estado": p.estado, "parametros_arrastrados": arrastrados}


@app.get(API + "/empresas/{empresa_id}/periodos/{periodo_id}/parametros")
def ver_parametros(periodo_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """UIT, RMV, tasas y tasas por AFP vigentes en el periodo (editadas o arrastradas del mes anterior; si no, las del Diccionario)."""
    return parametros_periodo.efectivos(db, empresa, _periodo(db, empresa, periodo_id))


@app.put(API + "/empresas/{empresa_id}/periodos/{periodo_id}/parametros")
def guardar_parametros(periodo_id: int, data: ParametrosPeriodoIn, empresa: Empresa = Depends(empresa_actual),
                       cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    """Edita los parámetros del periodo ABIERTO. Los periodos siguientes los heredan al abrirse. Luego hay que Recalcular."""
    p = _periodo(db, empresa, periodo_id)
    r = parametros_periodo.guardar(db, empresa, p, data.parametros, data.afps)
    db.add(Auditoria(cuenta_id=cuenta.id, empresa_id=empresa.id, accion="EDITA_PARAMETROS_PERIODO", detalle=f"{p.mes:02d}/{p.anio}"))
    db.commit()
    return r


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
    bloqueados = L.bloqueados_del_periodo(db, p.id)
    for i, f in enumerate(filas, start=1):
        c = conceptos.get(f.codigo)
        if f.documento not in docs:
            errores.append({"fila": i, "error": "DNI inexistente"})
        elif docs[f.documento] in bloqueados:
            errores.append({"fila": i, "error": "Liquidación CERRADA: su boleta está bloqueada"})
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
    liqs = {l.empleado_id: l for l in db.query(Liquidacion).filter(Liquidacion.periodo_id == p.id, Liquidacion.estado == "CERRADA")}

    def fila(r):
        l = liqs.get(r.empleado_id)
        return {"empleado_id": r.empleado_id, "colaborador": (r.snapshot or {}).get("colaborador"),
                "documento": (r.snapshot or {}).get("documento"), "ingresos": r.remuneracion_computable,
                "descuentos": r.total_descuentos, "aportes_empleador": r.aportes_empleador, "neto": r.neto_a_pagar,
                "dias_trabajados": r.dias_trabajados, "liquidacion_estado": l.estado if l else None,
                "liquidacion_neto": l.neto_a_pagar if l else None,
                "total_consolidado": (Decimal(r.neto_a_pagar) + Decimal(l.neto_a_pagar)) if l else None}
    return {"estado": p.estado, "filas": [fila(r) for r in
                                          db.query(Resultado).filter(Resultado.periodo_id == p.id).order_by(Resultado.id)]}


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
             .filter(Detalle.periodo_id == p.id).group_by(Detalle.concepto_codigo, Detalle.categoria).all())
    acum = {(c, cat): [Decimal(str(t)), n] for c, cat, t, n in filas}
    neto = Decimal(str(db.query(func.coalesce(func.sum(Resultado.neto_a_pagar), 0)).filter(Resultado.periodo_id == p.id).scalar()))
    # lo CERRADO de las liquidaciones se suma; los cálculos sin cerrar no entran
    cerradas = db.query(Liquidacion).filter(Liquidacion.periodo_id == p.id, Liquidacion.estado == "CERRADA").all()
    for liq in cerradas:
        neto += Decimal(str(liq.neto_a_pagar))
        for d in db.query(LiquidacionDetalle).filter(LiquidacionDetalle.liquidacion_id == liq.id):
            fila = acum.setdefault((d.concepto_codigo, d.categoria), [Decimal(0), 0])
            fila[0] += Decimal(str(d.monto))
            fila[1] += 1
    orden = {"PARAMETRO": 0, "INGRESO": 1, "DESCUENTO": 2, "APORTE": 3}
    return {"neto_total": neto, "liquidaciones_incluidas": len(cerradas),
            "conceptos": [{"codigo": c, "descripcion": desc.get(c, ""), "categoria": cat, "total": t, "colaboradores": n}
                          for (c, cat), (t, n) in sorted(acum.items(), key=lambda kv: (orden.get(kv[0][1], 9), kv[0][0]))]}



@app.get(API + "/empresas/{empresa_id}/diccionario/parametros")
def parametros_vigentes(empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """Parámetros del Diccionario vigentes hoy (solo lectura: se cambian publicando una nueva versión)."""
    from .diccionario import cargar_parametros
    dic = db.get(Diccionario, empresa.diccionario_id)
    pr = cargar_parametros(db, empresa.diccionario_id, date.today())
    return {"diccionario": f"{dic.codigo} v{dic.version}", "parametros": {k: str(v) for k, v in pr.p.items()},
            "afps": [{"nombre": a.nombre, "aporte": a.aporte, "comision": a.comision, "comision_mixta_flujo": a.comision_mixta_flujo,
                      "comision_saldo": a.comision_saldo, "prima": a.prima, "tope_prima": a.tope_prima}
                     for a in sorted(pr.afps.values(), key=lambda x: x.nombre)]}


# ------------------------------------------------------------------ exportaciones
@app.get(API + "/empresas/{empresa_id}/planillas/{periodo_id}/exportar-afpnet")
def exportar_afpnet(periodo_id: int, formato: str = Query("xlsx", pattern="^(xlsx|csv)$"),
                    empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    p = _periodo(db, empresa, periodo_id)
    contenido, mime = exportadores.afpnet_bytes(exportadores.afpnet_filas(db, empresa, p), formato)
    nombre = f"AFPNET_{empresa.ruc}_{p.anio}_{p.mes:02d}.{formato}"
    return Response(contenido, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename={nombre}",
        "X-Avisos": _avisos_header(exportadores.avisos_consolidacion(db, p))})


@app.get(API + "/empresas/{empresa_id}/planillas/{periodo_id}/exportar-plame-zip")
def exportar_plame(periodo_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    p = _periodo(db, empresa, periodo_id)
    contenido, prefijo, sin_codigo = exportadores.plame_zip(db, empresa, p)
    return Response(contenido, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename=PLAME_{prefijo}.zip",
        "X-Avisos": _avisos_header(exportadores.avisos_consolidacion(db, p), sin_codigo)})



# ------------------------------------------------------------------ módulo Liquidaciones
# La liquidación NO se guarda como borrador: «Ver», PDF y Excel la calculan al pedirlos. Solo «Cerrar» la guarda, y solo lo
# cerrado viaja al AFPnet, al PLAME y a los resúmenes. «Reabrir» anula el cierre (con motivo) y vuelve a ser un cálculo.
def _descripciones(db: Session, periodo: Periodo) -> dict:
    return {c.codigo: c.descripcion for c in db.query(Concepto).filter(Concepto.diccionario_id == periodo.diccionario_id)}


def _doc_comun(periodo: Periodo, emp: Empleado) -> dict:
    return {"periodo": {"id": periodo.id, "anio": periodo.anio, "mes": periodo.mes, "estado": periodo.estado},
            "empleado_id": emp.id, "documento": emp.documento_identidad, "colaborador": f"{emp.apellidos}, {emp.nombres}",
            "fecha_ingreso": emp.fecha_ingreso, "fecha_cese": emp.fecha_cese, "plazo_pago": emp.fecha_cese + timedelta(days=2)}


def _doc_calculo(db: Session, periodo: Periodo, emp: Empleado, calc) -> dict:
    desc = _descripciones(db, periodo)
    return {**_doc_comun(periodo, emp), "estado": "SIN_CERRAR", "liquidacion_id": None, "motivo_cese": emp.motivo_cese,
            "clase_despido": emp.clase_despido, "tipo_tipo": None, "tipo_contrato": emp.tipo_contrato,
            "fecha_fin_contrato": emp.fecha_fin_contrato, "cts_anterior_aplica": calc.cts_anterior_aplica,
            "cts_anterior_pagada": calc.cts_anterior_pagada, "total_ingresos": calc.total_ingresos,
            "total_descuentos": calc.total_descuentos, "neto_a_pagar": calc.neto, "ret_5ta_ajuste": calc.ret_5ta_ajuste,
            "aporte_essalud": calc.aporte_essalud, "aporte_eps": calc.aporte_eps, "avisos": calc.avisos,
            "faltantes_para_cerrar": calc.faltantes, "puede_cerrar": not calc.faltantes and periodo.estado == "ABIERTO",
            "snapshot": calc.snapshot,
            "conceptos": [{"codigo": f["codigo"], "descripcion": desc.get(f["codigo"], ""), "categoria": f["categoria"],
                           "monto": f["monto"], "formula": f["formula"]} for f in calc.filas]}


def _doc_cerrada(db: Session, liq: Liquidacion, emp: Empleado) -> dict:
    periodo = db.get(Periodo, liq.periodo_id)
    desc = _descripciones(db, periodo)
    _, a_fin = R.semestre_cts_anterior(liq.fecha_cese)
    return {**_doc_comun(periodo, emp), "estado": "CERRADA", "liquidacion_id": liq.id, "motivo_cese": liq.motivo_cese,
            "clase_despido": liq.clase_despido, "tipo_contrato": liq.tipo_contrato, "fecha_fin_contrato": liq.fecha_fin_contrato,
            "cts_anterior_aplica": emp.fecha_ingreso <= a_fin, "cts_anterior_pagada": liq.cts_anterior_pagada,
            "total_ingresos": liq.total_ingresos, "total_descuentos": liq.total_descuentos, "neto_a_pagar": liq.neto_a_pagar,
            "ret_5ta_ajuste": liq.ret_5ta_ajuste, "aporte_essalud": liq.aporte_essalud, "aporte_eps": liq.aporte_eps,
            "avisos": liq.avisos or [], "faltantes_para_cerrar": [], "puede_cerrar": False, "snapshot": liq.snapshot,
            "conceptos": [{"codigo": d.concepto_codigo, "descripcion": desc.get(d.concepto_codigo, ""), "categoria": d.categoria,
                           "monto": d.monto, "formula": d.formula}
                          for d in db.query(LiquidacionDetalle).filter(LiquidacionDetalle.liquidacion_id == liq.id)
                          .order_by(LiquidacionDetalle.orden)]}


def _documento(db: Session, empresa: Empresa, periodo_id: int, empleado_id: int, cts_pagada: Optional[bool]) -> dict:
    """La liquidación CERRADA (guardada) si existe; si no, el cálculo de hoy sin guardar."""
    emp = _empleado(db, empresa, empleado_id)
    if emp.fecha_cese is None:
        raise HTTPException(422, "El colaborador no tiene fecha de cese.")
    liq = (db.query(Liquidacion).filter(Liquidacion.empleado_id == emp.id, Liquidacion.estado == "CERRADA",
                                        Liquidacion.fecha_cese == emp.fecha_cese).first())
    if liq:
        return _doc_cerrada(db, liq, emp)
    periodo = _periodo(db, empresa, periodo_id)
    return _doc_calculo(db, periodo, emp, L.calcular(db, periodo, emp, cts_pagada))


@app.get(API + "/empresas/{empresa_id}/liquidaciones")
def listar_liquidaciones(periodo_id: int, estado: Optional[str] = Query(None, pattern="^(TODAS|SIN_CERRAR|CERRADA)$"),
                         empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """Cesados del periodo (y pendientes de meses anteriores) con su estado: SIN_CERRAR o CERRADA."""
    p = _periodo(db, empresa, periodo_id)
    filas = L.listar(db, p, estado)
    cuenta = lambda e: sum(1 for f in filas if f["estado"] == e)
    return {"periodo": {"id": p.id, "anio": p.anio, "mes": p.mes, "estado": p.estado}, "filas": filas,
            "resumen": {"sin_cerrar": cuenta("SIN_CERRAR"), "cerradas": cuenta("CERRADA")}}


@app.get(API + "/empresas/{empresa_id}/liquidaciones/vista")
def ver_liquidacion(periodo_id: int, empleado_id: int, cts_anterior_pagada: Optional[bool] = None,
                    empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """«Ver»: calcula al momento (sin guardar nada) o muestra la liquidación cerrada."""
    return _documento(db, empresa, periodo_id, empleado_id, cts_anterior_pagada)


@app.get(API + "/empresas/{empresa_id}/liquidaciones/pdf")
def pdf_de_liquidacion(periodo_id: int, empleado_id: int, cts_anterior_pagada: Optional[bool] = None, formulas: bool = False,
                       firmas: bool = True, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """PDF con el formato de la empresa. Sin cerrar, sale como BORRADOR y no se guarda nada."""
    doc = _documento(db, empresa, periodo_id, empleado_id, cts_anterior_pagada)
    contenido = pdf_liquidacion.generar_pdf(empresa, doc, formulas, firmas)
    nombre = f"LIQUIDACION_{empresa.ruc}_{doc['documento']}_{doc['periodo']['anio']}-{doc['periodo']['mes']:02d}" + ("" if doc["estado"] == "CERRADA" else "_BORRADOR")
    return Response(contenido, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={nombre}.pdf"})


@app.get(API + "/empresas/{empresa_id}/liquidaciones/excel")
def excel_de_liquidacion(periodo_id: int, empleado_id: int, cts_anterior_pagada: Optional[bool] = None,
                         empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    doc = _documento(db, empresa, periodo_id, empleado_id, cts_anterior_pagada)
    contenido = pdf_liquidacion.generar_excel(empresa, doc)
    nombre = f"LIQUIDACION_{empresa.ruc}_{doc['documento']}_{doc['periodo']['anio']}-{doc['periodo']['mes']:02d}" + ("" if doc["estado"] == "CERRADA" else "_BORRADOR")
    return Response(contenido, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={nombre}.xlsx"})


@app.post(API + "/empresas/{empresa_id}/liquidaciones/cerrar")
def cerrar_liquidacion(data: LiquidacionCerrarIn, empresa: Empresa = Depends(empresa_actual),
                       cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    """Cierre DEFINITIVO: lo único que se guarda. Desde aquí viaja al AFPnet, al PLAME y a los resúmenes."""
    periodo = _periodo(db, empresa, data.periodo_id)
    emp = _empleado(db, empresa, data.empleado_id)
    liq = L.cerrar(db, periodo, emp, cuenta.id, data.cts_anterior_pagada)
    return _doc_cerrada(db, liq, emp)


@app.post(API + "/empresas/{empresa_id}/liquidaciones/{liquidacion_id}/reabrir")
def reabrir_liquidacion(liquidacion_id: int, data: ReaperturaIn, empresa: Empresa = Depends(empresa_actual),
                        cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    """Anula el cierre: deja de viajar a PLAME, AFPnet y resúmenes y vuelve a ser un cálculo sin guardar."""
    liq = (db.query(Liquidacion).join(Periodo, Periodo.id == Liquidacion.periodo_id)
           .filter(Liquidacion.id == liquidacion_id, Periodo.empresa_id == empresa.id).first())
    if not liq:
        raise HTTPException(404, "Liquidación no encontrada.")
    L.reabrir(db, liq, db.get(Periodo, liq.periodo_id), cuenta.id, data.motivo)
    return {"ok": True, "estado": "SIN_CERRAR"}


# ------------------------------------------------------------------ boletas mensuales en PDF (formato de la empresa)
@app.get(API + "/empresas/{empresa_id}/planillas/{periodo_id}/boletas/pdf")
def boletas_pdf(periodo_id: int, empleado_id: Optional[int] = None, empresa: Empresa = Depends(empresa_actual),
                db: Session = Depends(get_db)):
    """Una boleta (o todas las del periodo), dos copias por hoja. Requiere haber calculado el periodo."""
    p = _periodo(db, empresa, periodo_id)
    q = (db.query(Resultado, Empleado).join(Empleado, Empleado.id == Resultado.empleado_id)
         .filter(Resultado.periodo_id == p.id).order_by(Empleado.apellidos, Empleado.nombres))
    if empleado_id:
        q = q.filter(Empleado.id == empleado_id)
    filas = q.all()
    if not filas:
        raise HTTPException(409, "El periodo no tiene resultados: ejecute Recalcular primero.")
    desc = _descripciones(db, p)
    items = []
    for res, emp in filas:
        det = [{"codigo": d.concepto_codigo, "categoria": d.categoria, "monto": d.monto, "formula": d.formula}
               for d in db.query(Detalle).filter(Detalle.periodo_id == p.id, Detalle.empleado_id == emp.id).order_by(Detalle.id)]
        items.append({"empleado": emp, "resultado": res, "detalle": det, "descripciones": desc, "snapshot": res.snapshot})
    contenido = pdf_boleta.generar(empresa, p, items)
    nombre = f"BOLETAS_{empresa.ruc}_{p.anio}_{p.mes:02d}" + (f"_{filas[0][1].documento_identidad}" if empleado_id else "")
    return Response(contenido, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={nombre}.pdf"})


# ------------------------------------------------------------------ CTS semestral
@app.post(API + "/empresas/{empresa_id}/cts-semestral/calcular")
def cts_calcular(data: CtsCalcularIn, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    """Calcula la CTS del semestre (NOV = mayo-octubre; MAY = noviembre-abril) de los colaboradores vigentes al cierre."""
    return semestrales.calcular_cts(db, empresa, data.anio, data.semestre)


@app.get(API + "/empresas/{empresa_id}/cts-semestral")
def cts_listar(anio: int, semestre: str = Query(pattern="^(NOV|MAY)$"), empresa: Empresa = Depends(empresa_actual),
               db: Session = Depends(get_db)):
    return semestrales.listar_cts(db, empresa, anio, semestre)


@app.post(API + "/empresas/{empresa_id}/cts-semestral/depositar")
def cts_depositar(data: CtsDepositarIn, empresa: Empresa = Depends(empresa_actual), cuenta: Cuenta = Depends(get_cuenta),
                  db: Session = Depends(get_db)):
    return semestrales.marcar_depositada(db, empresa, data.anio, data.semestre, data.fecha_deposito, data.empleado_ids, cuenta.id)


@app.post(API + "/empresas/{empresa_id}/cts-semestral/revertir")
def cts_revertir(data: CtsRevertirIn, empresa: Empresa = Depends(empresa_actual), cuenta: Cuenta = Depends(get_cuenta),
                 db: Session = Depends(get_db)):
    return semestrales.revertir_deposito(db, empresa, data.anio, data.semestre, data.empleado_ids, cuenta.id)


@app.get(API + "/empresas/{empresa_id}/cts-semestral/exportar")
def cts_exportar(anio: int, semestre: str = Query(pattern="^(NOV|MAY)$"), empresa: Empresa = Depends(empresa_actual),
                 db: Session = Depends(get_db)):
    contenido = semestrales.excel_cts(db, empresa, anio, semestre)
    return Response(contenido, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=CTS_{semestre}_{anio}_{empresa.ruc}.xlsx"})


# ------------------------------------------------------------------ reingreso (nuevo ciclo laboral)
@app.post(API + "/empresas/{empresa_id}/empleados/{empleado_id}/reingreso")
def reingreso(empleado_id: int, data: ReingresoIn, empresa: Empresa = Depends(empresa_actual),
              cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)):
    """Nuevo vínculo laboral: conserva el historial del ciclo anterior y abre una nueva fecha de ingreso."""
    e = _empleado(db, empresa, empleado_id)
    if e.estado != "CESADO":
        raise HTTPException(409, "Solo se puede reingresar a un colaborador cesado.")
    if data.fecha_ingreso <= e.fecha_cese:
        raise HTTPException(422, f"La nueva fecha de ingreso debe ser posterior al cese ({e.fecha_cese}).")
    activos = db.query(Empleado).filter(Empleado.empresa_id == empresa.id, Empleado.fecha_cese.is_(None)).count()
    if activos >= cuenta.max_trabajadores:
        raise HTTPException(403, f"Tu plan {cuenta.plan} permite hasta {cuenta.max_trabajadores} trabajadores por empresa.")
    cerrada = db.query(Liquidacion).filter(Liquidacion.empleado_id == e.id, Liquidacion.estado == "CERRADA",
                                           Liquidacion.fecha_cese == e.fecha_cese).first()
    db.add(Vinculo(empleado_id=e.id, fecha_ingreso=e.fecha_ingreso, fecha_cese=e.fecha_cese,
                   motivo_cese=e.motivo_cese, clase_despido=e.clase_despido))
    e.fecha_ingreso, e.fecha_cese, e.motivo_cese, e.clase_despido, e.estado = data.fecha_ingreso, None, None, None, "ACTIVO"
    if data.sueldo_basico:
        if not db.query(Sueldo).filter(Sueldo.empleado_id == e.id, Sueldo.fecha_vigencia == data.fecha_ingreso).first():
            db.add(Sueldo(empleado_id=e.id, monto=Decimal(str(data.sueldo_basico)), fecha_vigencia=data.fecha_ingreso,
                          motivo="Sueldo del reingreso"))
    db.add(Auditoria(cuenta_id=cuenta.id, empresa_id=empresa.id, accion="REINGRESO", detalle=f"empleado {e.id} desde {data.fecha_ingreso}"))
    db.commit()
    return {"estado": e.estado, "fecha_ingreso": e.fecha_ingreso,
            "aviso": None if cerrada else "El ciclo anterior no tiene una liquidación CERRADA registrada en el sistema."}


@app.get(API + "/empresas/{empresa_id}/empleados/{empleado_id}/vinculos")
def vinculos(empleado_id: int, empresa: Empresa = Depends(empresa_actual), db: Session = Depends(get_db)):
    e = _empleado(db, empresa, empleado_id)
    return [{"fecha_ingreso": v.fecha_ingreso, "fecha_cese": v.fecha_cese, "motivo_cese": v.motivo_cese, "clase_despido": v.clase_despido}
            for v in db.query(Vinculo).filter(Vinculo.empleado_id == e.id).order_by(Vinculo.fecha_ingreso)]
