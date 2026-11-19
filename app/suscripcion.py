"""Vigencia de los planes (Planilla Ágil): fecha de corte por cuenta, días de gracia y solo lectura.

Reglas (decididas por el dueño):
  · Cada cuenta tiene su propia fecha de vencimiento (vence_el). Sin fecha = sin vencimiento (p. ej. plan GRATIS).
  · Aviso desde 15 días antes. Al vencer, 7 días de gracia con todo normal (gracia_dias, editable por cuenta).
  · Pasada la gracia: solo lectura (consultar y descargar), no calcular, cerrar ni editar. Nunca se borran datos.
  · Renovar suma el período desde la fecha de corte vigente (no se pierden días); si ya venció, desde hoy.
  · El administrador puede suspender (activa = False): sin acceso."""
import calendar
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from .models import Auditoria, ConfigComercial, Cuenta, Pago, PlanCatalogo

AVISO_DIAS = 15
# Planes comerciales por defecto (precio SIN IGV). Se siembran una vez y luego el administrador los edita en «Planes y precios».
DEFAULT_PLANES = [  # codigo, nombre, precio, RUC, trabajadores por RUC, orden, activo, destacado
    ("GRATIS", "Gratis", 0, 1, 10, 0, True, False),
    ("INICIO", "Inicio", 29, 1, 50, 1, True, False),
    ("ESTUDIO", "Estudio", 39, 3, 50, 2, True, True),
    ("PROFESIONAL", "Profesional", 59, 3, 100, 3, True, False),
    ("EMPRESARIAL", "Empresarial", 69, 4, 100, 4, True, False),
    ("FREELANCER", "Freelancer (antiguo)", 0, 20, 10, 9, False, False),
]
# Valores PROPUESTOS (editables): descuento anual de la imagen de referencia; adicionales de la opción C; WhatsApp por definir.
DEFAULT_CONFIG = {"WHATSAPP": "", "DSCTO_ANUAL_PCT": "15", "EXTRA_RUC": "10", "EXTRA_TRABAJADOR": "0.60", "PRECIOS_INCLUYEN_IGV": "0"}
ESTADOS = ("VIGENTE", "POR_VENCER", "EN_GRACIA", "VENCIDA", "SUSPENDIDA", "SIN_VENCIMIENTO")


def estado(c: Cuenta, hoy: Optional[date] = None) -> str:
    hoy = hoy or date.today()
    if not c.activa:
        return "SUSPENDIDA"
    if c.vence_el is None:
        return "SIN_VENCIMIENTO"
    dias = (c.vence_el - hoy).days
    if dias > AVISO_DIAS:
        return "VIGENTE"
    if dias >= 0:
        return "POR_VENCER"
    if -dias <= (c.gracia_dias if c.gracia_dias is not None else 7):
        return "EN_GRACIA"
    return "VENCIDA"


def dias_restantes(c: Cuenta, hoy: Optional[date] = None) -> Optional[int]:
    return None if c.vence_el is None else (c.vence_el - (hoy or date.today())).days


def solo_lectura(c: Cuenta) -> bool:
    return estado(c) == "VENCIDA"


def sumar_meses(d: date, meses: int) -> date:
    m = d.month - 1 + meses
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def renovar(db: Session, c: Cuenta, meses: int, plan: Optional[str] = None, monto=None, referencia: Optional[str] = None,
            nota: Optional[str] = None, metodo: str = "MANUAL", hoy: Optional[date] = None) -> Pago:
    hoy = hoy or date.today()
    antes = c.vence_el
    base = antes if (antes is not None and antes >= hoy) else hoy
    c.vence_el = sumar_meses(base, meses)
    if plan and plan != c.plan:
        c.plan = plan
        lim = limites(db, plan)
        if lim:
            c.max_empresas, c.max_trabajadores = lim
    c.activa = True
    pago = Pago(cuenta_id=c.id, fecha=hoy, plan=c.plan, meses=meses, monto=None if monto in (None, "") else Decimal(str(monto)),
                metodo=metodo, referencia=(referencia or None), vence_antes=antes, vence_despues=c.vence_el, nota=(nota or None))
    db.add(pago)
    db.add(Auditoria(cuenta_id=c.id, accion="ADMIN_RENUEVA_PLAN", detalle=f"{meses} mes(es) · {c.plan} · vence {c.vence_el}"))
    return pago


# ------------------------------------------------------------------ catálogo de planes y precios
def sembrar_planes(db: Session):
    """Crea los planes y la configuración comercial que falten (nunca pisa lo que el administrador ya editó)."""
    existentes = {p.codigo for p in db.query(PlanCatalogo)}
    for cod, nombre, precio, me, mt, orden, activo, dest in DEFAULT_PLANES:
        if cod not in existentes:
            db.add(PlanCatalogo(codigo=cod, nombre=nombre, precio=Decimal(precio), max_empresas=me, max_trabajadores=mt, orden=orden, activo=activo, destacado=dest))
    claves = {c.clave for c in db.query(ConfigComercial)}
    for k, v in DEFAULT_CONFIG.items():
        if k not in claves:
            db.add(ConfigComercial(clave=k, valor=v))
    db.commit()


def _asegurar(db: Session):
    if db.query(PlanCatalogo).first() is None:          # primera vez (o base de pruebas): se siembra el catálogo por defecto
        sembrar_planes(db)


def config(db: Session) -> dict:
    _asegurar(db)
    out = dict(DEFAULT_CONFIG)
    out.update({c.clave: c.valor for c in db.query(ConfigComercial)})
    return out


def plan(db: Session, codigo: str) -> Optional[PlanCatalogo]:
    return db.get(PlanCatalogo, codigo)


def plan_valido(db: Session, codigo: str) -> bool:
    _asegurar(db)
    return db.get(PlanCatalogo, codigo) is not None


def limites(db: Session, codigo: str):
    _asegurar(db)
    p = db.get(PlanCatalogo, codigo)
    return (p.max_empresas, p.max_trabajadores) if p else None


def precio_cuenta(db: Session, c: Cuenta, cfg: Optional[dict] = None, catalogo: Optional[dict] = None) -> Decimal:
    """Precio mensual (sin IGV) = precio del plan + adicionales: RUC y trabajadores por encima de los que incluye el plan (opción C)."""
    cfg = cfg or config(db)
    p = (catalogo or {}).get(c.plan) or db.get(PlanCatalogo, c.plan)
    if p is None:
        return Decimal(0)
    extra_ruc = max(0, (c.max_empresas or 0) - p.max_empresas)
    extra_trab = max(0, (c.max_trabajadores or 0) - p.max_trabajadores) * max(1, c.max_empresas or 1)
    if p.precio == 0 and c.plan == "GRATIS":
        return Decimal(0)
    return (Decimal(p.precio) + extra_ruc * Decimal(cfg["EXTRA_RUC"]) + extra_trab * Decimal(cfg["EXTRA_TRABAJADOR"])).quantize(Decimal("0.01"))
