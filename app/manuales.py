"""Datos adicionales de la liquidación: ingresos y descuentos MANUALES (suma graciosa, descuentos otros, etc.) ligados a la tabla de
conceptos del Diccionario. Participan en el cálculo global de la liquidación (totales, bases afectas, AFP, EsSalud, 5ta) y, al cerrar la
liquidación, quedan guardados con ella. Mientras la liquidación esté CERRADA no se pueden agregar, editar ni borrar."""
from decimal import Decimal
from typing import List

from sqlalchemy.orm import Session

from .diccionario import ErrorDeNegocio
from .models import Concepto, Empleado, Liquidacion, LiquidacionManual

# Conceptos que la liquidación calcula sola o que no tienen sentido cargar a mano aquí
NO_ELEGIBLES = {"@GRATIFICACION", "@BONIF_EXTRA_29351", "@INGRESO_5TA_DIRECTA"}
PRIMEROS = {"@SUMA_GRACIOSA", "@DESCUENTOS_OTROS"}


def catalogo(db: Session, diccionario_id: int) -> List[dict]:
    """Conceptos del Diccionario que se pueden cargar a mano: ingresos y descuentos que el motor NO calcula."""
    q = (db.query(Concepto).filter(Concepto.diccionario_id == diccionario_id, Concepto.categoria.in_(("INGRESO", "DESCUENTO")),
                                   Concepto.calculado.is_(False)).order_by(Concepto.categoria, Concepto.descripcion))
    lista = [{"codigo": c.codigo, "descripcion": c.descripcion, "categoria": c.categoria, "afecta_afp": bool(c.afecta_afp_onp),
              "afecta_essalud": bool(c.afecta_essalud), "afecta_5ta": bool(c.afecta_5ta_regular), "codigo_plame": c.codigo_plame}
             for c in q if c.codigo not in NO_ELEGIBLES]
    # los conceptos genéricos de la liquidación (suma graciosa, descuentos otros) van primero; el resto, por descripción
    lista.sort(key=lambda c: (c["categoria"], 0 if c["codigo"] in PRIMEROS else 1, c["descripcion"]))
    return lista


def concepto_valido(db: Session, diccionario_id: int, codigo: str) -> dict:
    for c in catalogo(db, diccionario_id):
        if c["codigo"] == codigo:
            return c
    raise ErrorDeNegocio(f"El concepto {codigo} no está disponible para datos adicionales (debe ser un ingreso o descuento no calculado del Diccionario).", 422)


def fila(m: LiquidacionManual, cat: dict) -> dict:
    return {"id": m.id, "concepto_codigo": m.concepto_codigo, "descripcion": m.descripcion, "categoria": cat.get("categoria", "INGRESO"),
            "monto": m.monto, "nota": m.nota, "afecta_afp": m.afecta_afp, "afecta_essalud": m.afecta_essalud, "afecta_5ta": m.afecta_5ta,
            "codigo_plame": cat.get("codigo_plame")}


def listar(db: Session, emp: Empleado, diccionario_id: int) -> dict:
    cat = {c["codigo"]: c for c in catalogo(db, diccionario_id)}
    todos = {c.codigo: c for c in db.query(Concepto).filter(Concepto.diccionario_id == diccionario_id)}
    filas = []
    for m in (db.query(LiquidacionManual).filter(LiquidacionManual.empleado_id == emp.id, LiquidacionManual.fecha_cese == emp.fecha_cese)
              .order_by(LiquidacionManual.id)):
        c = cat.get(m.concepto_codigo) or ({"categoria": todos[m.concepto_codigo].categoria, "codigo_plame": todos[m.concepto_codigo].codigo_plame}
                                           if m.concepto_codigo in todos else {})
        filas.append(fila(m, c))
    suma = lambda cat_: sum((Decimal(str(f["monto"])) for f in filas if f["categoria"] == cat_), Decimal(0))
    cerrada = db.query(Liquidacion).filter(Liquidacion.empleado_id == emp.id, Liquidacion.estado == "CERRADA",
                                           Liquidacion.fecha_cese == emp.fecha_cese).first()
    return {"fecha_cese": emp.fecha_cese, "bloqueado": cerrada is not None, "filas": filas, "catalogo": list(cat.values()),
            "total_ingresos": suma("INGRESO"), "total_descuentos": suma("DESCUENTO")}


def limpiar(db: Session, emp: Empleado, salvo_fecha=None) -> int:
    """Borra los datos adicionales de un cese que ya no existe (reactivación, nuevo cese, reingreso). Nunca toca los de una liquidación CERRADA."""
    n = 0
    for m in db.query(LiquidacionManual).filter(LiquidacionManual.empleado_id == emp.id).all():
        if salvo_fecha is not None and m.fecha_cese == salvo_fecha:
            continue
        cerrada = db.query(Liquidacion).filter(Liquidacion.empleado_id == emp.id, Liquidacion.estado == "CERRADA",
                                               Liquidacion.fecha_cese == m.fecha_cese).first()
        if cerrada is None:
            db.delete(m)
            n += 1
    return n
