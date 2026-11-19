"""Regímenes laborales y qué plan los habilita. Un régimen se puede usar cuando (1) el plan lo incluye y (2) sus reglas
(Diccionario) ya están cargadas en el sistema. Los planes de pago se ajustan aquí, en un solo lugar."""
from sqlalchemy.orm import Session

from .diccionario import ErrorDeNegocio
from .models import Diccionario

REGIMENES = {
    "RG_728": "Régimen General (D.Leg. 728)",
    "MYPE_MICRO": "MYPE · Microempresa",
    "MYPE_PEQUENA": "MYPE · Pequeña empresa",
    "CONSTRUCCION_CIVIL": "Construcción Civil (obreros)",
}
PLAN_SIN_CC = {"GRATIS"}                # el plan gratuito incluye Régimen General y MYPE (micro y pequeña); Construcción Civil requiere plan de pago


def permitido_por_plan(plan: str, codigo: str) -> bool:
    return codigo != "CONSTRUCCION_CIVIL" or plan not in PLAN_SIN_CC


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


ADVERTENCIAS = {
    "norma": {
        "DS_013_2013": "D.S. 013-2013-PRODUCE: es la norma que rige hoy.",
        "LEY_32353": "Ley 32353: solo entra en vigencia con su reglamento. Elíjala únicamente si su contador confirma que ya está vigente; "
                     "pensiones sociales y trabajo nocturno quedan por validar.",
    },
    "salud_micro": {
        "SIS": "SIS: S/ 15 al mes por trabajador (el Estado paga otros S/ 15). Solo salud, sin subsidios de EsSalud.",
        "ESSALUD": "EsSalud: 9 % de la remuneración (mínimo sobre la RMV). Incluye subsidios y prestaciones económicas.",
    },
    "asig_fam_mype": "Activada: se paga el 10 % de la RMV a los trabajadores marcados con asignación familiar. Valide con su contador si corresponde en MYPE.",
}


def version_dic(regimen: str, norma: str):
    """Versión del Diccionario: Régimen General = la última; MYPE = 1 (D.S. 013-2013) o 2 (Ley 32353)."""
    if regimen == "RG_728":
        return None
    if regimen == "CONSTRUCCION_CIVIL":
        return 1
    return 2 if norma == "LEY_32353" else 1
