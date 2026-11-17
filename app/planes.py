"""Regímenes laborales y qué plan los habilita. Un régimen se puede usar cuando (1) el plan lo incluye y (2) sus reglas
(Diccionario) ya están cargadas en el sistema. Los planes de pago se ajustan aquí, en un solo lugar."""
from sqlalchemy.orm import Session

from .diccionario import ErrorDeNegocio
from .models import Diccionario

REGIMENES = {
    "RG_728": "Régimen General (D.Leg. 728)",
    "MYPE_MICRO": "MYPE · Microempresa",
    "MYPE_PEQUENA": "MYPE · Pequeña empresa",
}
PLAN_SOLO_GENERAL = {"GRATIS"}          # el plan gratuito solo incluye el Régimen General; los demás planes incluyen todos


def permitido_por_plan(plan: str, codigo: str) -> bool:
    return codigo == "RG_728" or plan not in PLAN_SOLO_GENERAL


def estado_regimenes(db: Session, plan: str | None) -> list:
    """plan=None (administrador): sin restricción de plan."""
    con_reglas = {d.codigo for d in db.query(Diccionario).filter(Diccionario.estado == "ACTIVO")}
    out = []
    for cod, nombre in REGIMENES.items():
        perm = plan is None or permitido_por_plan(plan, cod)
        reglas = cod in con_reglas
        motivo = "" if (perm and reglas) else ("Requiere un plan superior." if not perm else "Próximamente: aún no están cargadas sus reglas.")
        out.append({"codigo": cod, "nombre": nombre, "permitido_por_plan": perm, "reglas_cargadas": reglas, "disponible": perm and reglas, "motivo": motivo})
    return out


def exigir_regimen(db: Session, plan: str | None, codigo: str):
    if codigo not in REGIMENES:
        raise ErrorDeNegocio(f"Régimen desconocido: {codigo}. Opciones: {', '.join(REGIMENES)}.", 422)
    r = next(x for x in estado_regimenes(db, plan) if x["codigo"] == codigo)
    if not r["permitido_por_plan"]:
        raise ErrorDeNegocio(f"El {REGIMENES[codigo]} no está incluido en su plan. Elija un plan superior para usarlo.", 403)
    if not r["reglas_cargadas"]:
        raise ErrorDeNegocio(f"El {REGIMENES[codigo]} aún no está disponible: sus reglas no están cargadas.", 422)
