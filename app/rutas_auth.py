"""Rutas de acceso: inicio/cierre de sesión, cambio y recuperación de contraseña, gestión de usuarios."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import usuarios as U
from .db import get_db
from .models import Auditoria, Cuenta, Usuario
from .legal import legal_pendiente
from .legal_textos import VERSION as LEGAL_VERSION
from .security import Acceso, exigir_admin, exigir_propietario, get_acceso

API = "/api/v1"
router = APIRouter()


class LoginIn(BaseModel):
    email: str = Field(max_length=150)
    clave: str = Field(max_length=200)


class OlvideIn(BaseModel):
    email: str = Field(max_length=150)


class RestablecerIn(BaseModel):
    token: str = Field(max_length=200)
    nueva: str = Field(max_length=200)


class CambioClaveIn(BaseModel):
    actual: str = Field(max_length=200)
    nueva: str = Field(max_length=200)


class UsuarioIn(BaseModel):
    email: str = Field(max_length=150)
    nombre: str = Field(min_length=2, max_length=150)
    rol: str = Field(default="OPERADOR", pattern="^(PROPIETARIO|OPERADOR|CONSULTA)$")


class UsuarioPatch(BaseModel):
    nombre: Optional[str] = Field(default=None, min_length=2, max_length=150)
    rol: Optional[str] = Field(default=None, pattern="^(PROPIETARIO|OPERADOR|CONSULTA)$")
    activo: Optional[bool] = None


def _ficha(u: Usuario) -> dict:
    return {"id": u.id, "email": u.email, "nombre": u.nombre, "rol": u.rol, "activo": u.activo,
            "debe_cambiar_clave": u.debe_cambiar_clave, "ultimo_acceso": u.ultimo_acceso}


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()


# ------------------------------------------------------------------ sesión
@router.post(API + "/auth/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = _ip(request)
    if U.limite_ip(ip):
        raise HTTPException(429, "Demasiados intentos desde su conexión. Espere unos minutos.")
    try:
        token, exp, u = U.iniciar_sesion(db, data.email, data.clave, ip)
    except U.ErrorAcceso as e:
        raise HTTPException(e.status, str(e))
    return {"token": token, "expira": exp, "usuario": _ficha(u), "cuenta": {"id": u.cuenta.id, "nombre": u.cuenta.nombre, "plan": u.cuenta.plan},
            "debe_cambiar_clave": u.debe_cambiar_clave, "legal_pendiente": legal_pendiente(db, u), "legal_version": LEGAL_VERSION}


@router.get(API + "/auth/yo")
def yo(acceso: Acceso = Depends(get_acceso), db: Session = Depends(get_db)):
    if not acceso.usuario:
        raise HTTPException(400, "Esta credencial no es de un usuario.")
    u = acceso.usuario
    return {"usuario": _ficha(u), "cuenta": {"id": u.cuenta.id, "nombre": u.cuenta.nombre, "plan": u.cuenta.plan},
            "legal_pendiente": legal_pendiente(db, u), "legal_version": LEGAL_VERSION}


@router.post(API + "/auth/logout")
def logout(acceso: Acceso = Depends(get_acceso), db: Session = Depends(get_db)):
    if acceso.token:
        U.cerrar_sesion(db, acceso.token)
    return {"ok": True}


@router.post(API + "/auth/cambiar-clave")
def cambiar_clave(data: CambioClaveIn, acceso: Acceso = Depends(get_acceso), db: Session = Depends(get_db)):
    u = acceso.usuario
    if not u:
        raise HTTPException(400, "Esta credencial no es de un usuario.")
    if not U.verificar_clave(data.actual, u.clave_hash):
        raise HTTPException(422, "La contraseña actual no es correcta.")
    msg = U.validar_clave_nueva(data.nueva, u.email)
    if msg:
        raise HTTPException(422, msg)
    u.clave_hash, u.debe_cambiar_clave = U.hash_clave(data.nueva), False
    U.cerrar_todas(db, u.id)
    db.add(Auditoria(cuenta_id=u.cuenta_id, accion="CLAVE_CAMBIADA", detalle=u.email))
    db.commit()
    token, exp, _ = U.iniciar_sesion(db, u.email, data.nueva)      # sesión nueva: las anteriores se cerraron
    return {"ok": True, "token": token, "expira": exp}


@router.post(API + "/auth/olvide")
def olvide(data: OlvideIn, request: Request, db: Session = Depends(get_db)):
    """Respuesta siempre igual: no revela si el correo existe."""
    if U.limite_ip(_ip(request) + "|reset", 5, 900):
        raise HTTPException(429, "Demasiadas solicitudes. Espere unos minutos.")
    u = db.query(Usuario).filter(Usuario.email == (data.email or "").strip().lower(), Usuario.activo.is_(True)).first()
    if u and U.correo_configurado():
        token = U.crear_reset(db, u)
        U.enviar_correo(u.email, "Recupere su contraseña · Planilla Ágil",
                        f"<p>Hola {u.nombre},</p><p>Para elegir una contraseña nueva ingrese a este enlace (vale {U.RESET_MIN} minutos):</p>"
                        f"<p><a href=\"{U.enlace_reset(token)}\">Restablecer contraseña</a></p><p>Si no lo solicitó, ignore este mensaje.</p>")
    return {"ok": True, "mensaje": "Si el correo está registrado, recibirá un enlace para elegir una contraseña nueva. "
                                   "Si no llega, pida a su propietario de cuenta una contraseña temporal."}


@router.post(API + "/auth/restablecer")
def restablecer(data: RestablecerIn, db: Session = Depends(get_db)):
    try:
        U.canjear_reset(db, data.token, data.nueva)
    except U.ErrorAcceso as e:
        raise HTTPException(e.status, str(e))
    return {"ok": True}


# ------------------------------------------------------------------ gestión de usuarios (propietario)
def _cuenta_de(acceso: Acceso, cuenta_id: Optional[int]) -> int:
    if acceso.admin:
        if not cuenta_id:
            raise HTTPException(400, "Indique la cuenta (cuenta_id).")
        return cuenta_id
    return acceso.cuenta.id


@router.get(API + "/usuarios")
def listar_usuarios(cuenta_id: Optional[int] = None, acceso: Acceso = Depends(exigir_propietario), db: Session = Depends(get_db)):
    cid = _cuenta_de(acceso, cuenta_id)
    return [_ficha(u) for u in db.query(Usuario).filter(Usuario.cuenta_id == cid).order_by(Usuario.nombre)]


def _crear(db: Session, cuenta_id: int, data: UsuarioIn) -> dict:
    email = data.email.strip().lower()
    if not U.email_valido(email):
        raise HTTPException(422, "El correo no es válido.")
    if db.query(Usuario).filter(Usuario.email == email).first():
        raise HTTPException(409, "Ya existe un usuario con ese correo.")
    temp = U.clave_temporal()
    u = Usuario(cuenta_id=cuenta_id, email=email, nombre=data.nombre.strip(), rol=data.rol, clave_hash=U.hash_clave(temp), debe_cambiar_clave=True)
    db.add(u)
    db.add(Auditoria(cuenta_id=cuenta_id, accion="USUARIO_CREADO", detalle=f"{email} {data.rol}"))
    db.commit()
    return {**_ficha(u), "clave_temporal": temp, "aviso": "Entregue esta contraseña temporal ahora: no se vuelve a mostrar. Se pedirá cambiarla al ingresar."}


@router.post(API + "/usuarios", status_code=201)
def crear_usuario(data: UsuarioIn, cuenta_id: Optional[int] = None, acceso: Acceso = Depends(exigir_propietario), db: Session = Depends(get_db)):
    return _crear(db, _cuenta_de(acceso, cuenta_id), data)


def _usuario_de(db: Session, acceso: Acceso, uid: int, cuenta_id: Optional[int]) -> Usuario:
    u = db.get(Usuario, uid)
    if not u or u.cuenta_id != _cuenta_de(acceso, cuenta_id):
        raise HTTPException(404, "Usuario no encontrado.")
    return u


def _propietarios_activos(db: Session, cid: int) -> int:
    return db.query(Usuario).filter(Usuario.cuenta_id == cid, Usuario.rol == "PROPIETARIO", Usuario.activo.is_(True)).count()


@router.put(API + "/usuarios/{uid}")
def editar_usuario(uid: int, data: UsuarioPatch, cuenta_id: Optional[int] = None, acceso: Acceso = Depends(exigir_propietario), db: Session = Depends(get_db)):
    u = _usuario_de(db, acceso, uid, cuenta_id)
    cambios = data.model_dump(exclude_unset=True)
    dejaria = (cambios.get("rol", u.rol) != "PROPIETARIO") or (cambios.get("activo", u.activo) is False)
    if u.rol == "PROPIETARIO" and u.activo and dejaria and _propietarios_activos(db, u.cuenta_id) <= 1:
        raise HTTPException(409, "La cuenta debe tener al menos un propietario activo.")
    for k, v in cambios.items():
        setattr(u, k, v)
    if cambios.get("activo") is False or "rol" in cambios:
        U.cerrar_todas(db, u.id)
    db.add(Auditoria(cuenta_id=u.cuenta_id, accion="USUARIO_EDITADO", detalle=f"{u.email} {cambios}"))
    db.commit()
    return _ficha(u)


@router.post(API + "/usuarios/{uid}/clave-temporal")
def clave_temporal_usuario(uid: int, cuenta_id: Optional[int] = None, acceso: Acceso = Depends(exigir_propietario), db: Session = Depends(get_db)):
    u = _usuario_de(db, acceso, uid, cuenta_id)
    temp = U.clave_temporal()
    u.clave_hash, u.debe_cambiar_clave, u.intentos_fallidos, u.bloqueado_hasta = U.hash_clave(temp), True, 0, None
    U.cerrar_todas(db, u.id)
    db.add(Auditoria(cuenta_id=u.cuenta_id, accion="CLAVE_TEMPORAL", detalle=u.email))
    db.commit()
    return {"clave_temporal": temp, "aviso": "Entregue esta contraseña temporal ahora: no se vuelve a mostrar."}


@router.post(API + "/admin/cuentas/{cuenta_id}/usuarios", status_code=201)
def admin_crear_usuario(cuenta_id: int, data: UsuarioIn, _: Acceso = Depends(exigir_admin), db: Session = Depends(get_db)):
    if not db.get(Cuenta, cuenta_id):
        raise HTTPException(404, "Cuenta no encontrada.")
    return _crear(db, cuenta_id, data)
