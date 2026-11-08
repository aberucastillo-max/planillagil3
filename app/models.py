"""Modelos SQLAlchemy.  Montos en Numeric (nunca Float).

Capas:
  - Cuenta / Empresa        -> multi-tenant (un freelancer = una cuenta con varias empresas/RUC)
  - Diccionario*            -> reglas versionadas por régimen (RG_728 hoy; otros después)
  - Empleado y novedades    -> datos de la empresa
  - Periodo / Detalle / Resultado -> cálculo, con snapshot para reproducir periodos cerrados
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _now():
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------- multi-tenant
class Cuenta(Base):
    __tablename__ = "cuentas"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False)
    plan = Column(String(20), nullable=False, default="GRATIS")
    max_empresas = Column(Integer, nullable=False, default=1)
    max_trabajadores = Column(Integer, nullable=False, default=10)  # por empresa
    api_key_hash = Column(String(64), unique=True, nullable=False)
    activa = Column(Boolean, nullable=False, default=True)
    creado_en = Column(DateTime(timezone=True), default=_now)


class Empresa(Base):
    __tablename__ = "empresas"
    id = Column(Integer, primary_key=True)
    cuenta_id = Column(Integer, ForeignKey("cuentas.id"), nullable=False, index=True)
    ruc = Column(String(11), nullable=False)
    razon_social = Column(String(255), nullable=False)
    diccionario_id = Column(Integer, ForeignKey("diccionarios.id"), nullable=False)
    creado_en = Column(DateTime(timezone=True), default=_now)
    __table_args__ = (UniqueConstraint("cuenta_id", "ruc", name="uq_empresa_cuenta_ruc"),)


class Auditoria(Base):
    __tablename__ = "auditoria"
    id = Column(Integer, primary_key=True)
    cuenta_id = Column(Integer, ForeignKey("cuentas.id"), index=True)
    empresa_id = Column(Integer, index=True)
    accion = Column(String(60), nullable=False)
    detalle = Column(Text)
    creado_en = Column(DateTime(timezone=True), default=_now)


# ----------------------------------------------------------------- DICCIONARIO
class Diccionario(Base):
    """Conjunto de reglas de un régimen/tipo de planilla. Ej.: RG_728 v1."""
    __tablename__ = "diccionarios"
    id = Column(Integer, primary_key=True)
    codigo = Column(String(30), nullable=False)          # RG_728, (futuro) MYPE, CONSTRUCCION_CIVIL...
    nombre = Column(String(150), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    vigente_desde = Column(Date, nullable=False)
    estado = Column(String(15), nullable=False, default="ACTIVO")
    __table_args__ = (UniqueConstraint("codigo", "version", name="uq_dic_codigo_version"),)


class DiccionarioParametro(Base):
    __tablename__ = "diccionario_parametros"
    id = Column(Integer, primary_key=True)
    diccionario_id = Column(Integer, ForeignKey("diccionarios.id"), nullable=False, index=True)
    clave = Column(String(40), nullable=False)           # UIT, RMV, ESSALUD_TASA...
    valor = Column(Numeric(14, 6), nullable=False)
    vigente_desde = Column(Date, nullable=False)
    vigente_hasta = Column(Date)
    nota = Column(String(200))


class DiccionarioAFP(Base):
    __tablename__ = "diccionario_afp"
    id = Column(Integer, primary_key=True)
    diccionario_id = Column(Integer, ForeignKey("diccionarios.id"), nullable=False, index=True)
    nombre = Column(String(30), nullable=False)          # HABITAT, INTEGRA, PRIMA, PROFUTURO
    aporte = Column(Numeric(7, 4), nullable=False)
    comision = Column(Numeric(7, 4), nullable=False)
    prima = Column(Numeric(7, 4), nullable=False)
    tope_prima = Column(Numeric(12, 2))
    vigente_desde = Column(Date, nullable=False)
    vigente_hasta = Column(Date)


class DiccionarioEscala5ta(Base):
    __tablename__ = "diccionario_escala_5ta"
    id = Column(Integer, primary_key=True)
    diccionario_id = Column(Integer, ForeignKey("diccionarios.id"), nullable=False, index=True)
    desde_uit = Column(Numeric(6, 1), nullable=False)
    hasta_uit = Column(Numeric(6, 1))                    # NULL = sin tope
    tasa = Column(Numeric(5, 4), nullable=False)


class Concepto(Base):
    __tablename__ = "diccionario_conceptos"
    id = Column(Integer, primary_key=True)
    diccionario_id = Column(Integer, ForeignKey("diccionarios.id"), nullable=False, index=True)
    codigo = Column(String(50), nullable=False)          # @HABER_BASICO
    descripcion = Column(String(150), nullable=False)
    categoria = Column(String(12), nullable=False)       # PARAMETRO | INGRESO | DESCUENTO | APORTE
    naturaleza = Column(String(10), nullable=False)      # FIJO | VARIABLE | FORMULA
    calculado = Column(Boolean, nullable=False, default=False)  # True = lo calcula el motor (no admite carga)
    afecta_afp_onp = Column(Boolean, nullable=False, default=False)
    afecta_5ta_regular = Column(Boolean, nullable=False, default=False)
    afecta_5ta_directa = Column(Boolean, nullable=False, default=False)
    afecta_essalud = Column(Boolean, nullable=False, default=False)
    codigo_plame = Column(String(6))                     # Tabla 22 PLAME (validar contra norma vigente)
    __table_args__ = (UniqueConstraint("diccionario_id", "codigo", name="uq_concepto_dic_codigo"),)


class TipoSuspension(Base):
    __tablename__ = "diccionario_suspensiones"
    id = Column(Integer, primary_key=True)
    diccionario_id = Column(Integer, ForeignKey("diccionarios.id"), nullable=False, index=True)
    codigo_plame = Column(String(4), nullable=False)
    descripcion = Column(String(255), nullable=False)
    tipo = Column(String(12), nullable=False)            # PERFECTA | IMPERFECTA | SUBSIDIADA
    concepto_codigo = Column(String(50), nullable=False)
    __table_args__ = (UniqueConstraint("diccionario_id", "codigo_plame", name="uq_susp_dic_codigo"),)


# ----------------------------------------------------------------- personal
class Empleado(Base):
    __tablename__ = "empleados"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresas.id"), nullable=False)
    nombres = Column(String(100), nullable=False)
    apellidos = Column(String(100), nullable=False)
    tipo_documento = Column(String(10), nullable=False, default="DNI")
    documento_identidad = Column(String(15), nullable=False)
    regimen_codigo = Column(String(30), nullable=False, default="RG_728")
    puesto = Column(String(150))
    sede = Column(String(100))
    fecha_ingreso = Column(Date, nullable=False)
    fecha_cese = Column(Date)
    motivo_cese = Column(String(5))                      # PLAME Tabla 17
    sistema_pensionario = Column(String(3), nullable=False, default="ONP")  # AFP | ONP
    afp_nombre = Column(String(30))
    cuspp = Column(String(15))
    banco = Column(String(60))
    numero_cuenta = Column(String(50))
    asignacion_familiar = Column(Boolean, nullable=False, default=False)
    cobertura_eps = Column(Boolean, nullable=False, default=False)
    estado = Column(String(10), nullable=False, default="ACTIVO")
    __table_args__ = (
        UniqueConstraint("empresa_id", "tipo_documento", "documento_identidad", name="uq_emp_doc"),
        Index("ix_emp_empresa", "empresa_id", "estado"),
    )


class Sueldo(Base):
    """Historial. El sueldo vigente se DERIVA por fecha; no hay bandera que pueda quedar desfasada."""
    __tablename__ = "sueldos"
    id = Column(Integer, primary_key=True)
    empleado_id = Column(Integer, ForeignKey("empleados.id"), nullable=False, index=True)
    monto = Column(Numeric(10, 2), nullable=False)
    fecha_vigencia = Column(Date, nullable=False)
    motivo = Column(String(150))
    creado_en = Column(DateTime(timezone=True), default=_now)
    __table_args__ = (UniqueConstraint("empleado_id", "fecha_vigencia", name="uq_sueldo_fecha"),)


class Beneficio(Base):
    __tablename__ = "beneficios"
    id = Column(Integer, primary_key=True)
    empleado_id = Column(Integer, ForeignKey("empleados.id"), nullable=False, index=True)
    tipo = Column(String(30), nullable=False)            # EPS -> @DESCUENTO_EPS
    fecha_inicio = Column(Date, nullable=False)
    fecha_termino = Column(Date)
    monto = Column(Numeric(10, 2), nullable=False, default=0)


class Ausentismo(Base):
    __tablename__ = "ausentismos"
    id = Column(Integer, primary_key=True)
    empleado_id = Column(Integer, ForeignKey("empleados.id"), nullable=False, index=True)
    tipo_suspension_id = Column(Integer, ForeignKey("diccionario_suspensiones.id"), nullable=False)
    fecha_inicio = Column(Date, nullable=False)
    fecha_fin = Column(Date, nullable=False)
    estado = Column(String(12), nullable=False, default="REGISTRADO")


class Vacacion(Base):
    __tablename__ = "vacaciones"
    id = Column(Integer, primary_key=True)
    empleado_id = Column(Integer, ForeignKey("empleados.id"), nullable=False, index=True)
    fecha_inicio = Column(Date, nullable=False)
    fecha_fin = Column(Date, nullable=False)
    dias_vendidos = Column(Integer, nullable=False, default=0)
    estado = Column(String(12), nullable=False, default="REGISTRADO")


class RentaPrevia5ta(Base):
    __tablename__ = "rentas_previas_5ta"
    id = Column(Integer, primary_key=True)
    empleado_id = Column(Integer, ForeignKey("empleados.id"), nullable=False, index=True)
    anio = Column(Integer, nullable=False)
    mes = Column(Integer, nullable=False)                # mes desde el cual se aplica
    ruc = Column(String(11), nullable=False)
    razon_social = Column(String(255), nullable=False)
    ingreso_afecto = Column(Numeric(12, 2), nullable=False)
    retencion = Column(Numeric(12, 2), nullable=False)


# ----------------------------------------------------------------- planilla
class Periodo(Base):
    __tablename__ = "periodos"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresas.id"), nullable=False, index=True)
    anio = Column(Integer, nullable=False)
    mes = Column(Integer, nullable=False)
    fase = Column(String(30), nullable=False, default="Fin de Mes")
    fecha_inicio = Column(Date, nullable=False)
    fecha_fin = Column(Date, nullable=False)
    diccionario_id = Column(Integer, ForeignKey("diccionarios.id"), nullable=False)  # versión usada
    estado = Column(String(10), nullable=False, default="ABIERTO")                   # ABIERTO | CERRADO
    cerrado_en = Column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("empresa_id", "anio", "mes", "fase", name="uq_periodo"),)


class Novedad(Base):
    """Datos digitados/cargados por el usuario. Recalcular NO los borra."""
    __tablename__ = "novedades"
    id = Column(Integer, primary_key=True)
    periodo_id = Column(Integer, ForeignKey("periodos.id"), nullable=False, index=True)
    empleado_id = Column(Integer, ForeignKey("empleados.id"), nullable=False)
    concepto_codigo = Column(String(50), nullable=False)
    valor = Column(Numeric(12, 2), nullable=False)
    __table_args__ = (UniqueConstraint("periodo_id", "empleado_id", "concepto_codigo", name="uq_novedad"),)


class Detalle(Base):
    __tablename__ = "planilla_detalle"
    id = Column(Integer, primary_key=True)
    periodo_id = Column(Integer, ForeignKey("periodos.id"), nullable=False, index=True)
    empleado_id = Column(Integer, ForeignKey("empleados.id"), nullable=False)
    concepto_codigo = Column(String(50), nullable=False)
    categoria = Column(String(12), nullable=False)
    monto = Column(Numeric(12, 2), nullable=False)
    origen = Column(String(10), nullable=False, default="CALCULO")   # CALCULO | NOVEDAD
    formula = Column(Text)                                           # explica el cálculo ("¿por qué este monto?")
    __table_args__ = (Index("ix_detalle_pe", "periodo_id", "empleado_id"),)


class Resultado(Base):
    __tablename__ = "planilla_resultados"
    id = Column(Integer, primary_key=True)
    periodo_id = Column(Integer, ForeignKey("periodos.id"), nullable=False)
    empleado_id = Column(Integer, ForeignKey("empleados.id"), nullable=False)
    remuneracion_computable = Column(Numeric(12, 2), nullable=False)
    total_descuentos = Column(Numeric(12, 2), nullable=False)
    neto_a_pagar = Column(Numeric(12, 2), nullable=False)
    aportes_empleador = Column(Numeric(12, 2), nullable=False, default=0)
    base_previsional = Column(Numeric(12, 2), nullable=False, default=0)
    base_essalud = Column(Numeric(12, 2), nullable=False, default=0)
    base_5ta_regular = Column(Numeric(12, 2), nullable=False, default=0)
    ret_5ta_regular = Column(Numeric(12, 2), nullable=False, default=0)
    ret_5ta_directa = Column(Numeric(12, 2), nullable=False, default=0)
    dias_trabajados = Column(Integer, nullable=False, default=0)
    dias_subsidio = Column(Integer, nullable=False, default=0)
    horas_extras = Column(Numeric(8, 2), nullable=False, default=0)
    snapshot = Column(JSON)   # datos del colaborador y reglas usadas al calcular (reproducible aunque cambien después)
    __table_args__ = (UniqueConstraint("periodo_id", "empleado_id", name="uq_resultado"),)
