import re
from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

MONEY = re.compile(r"^-?\d+(\.\d{1,2})?$")


class EmpresaIn(BaseModel):
    ruc: str = Field(pattern=r"^\d{11}$")
    razon_social: str = Field(min_length=2, max_length=255)
    diccionario_codigo: str = "RG_728"
    cuenta_id: Optional[int] = None        # solo lo usa el administrador, para elegir la cuenta dueña
    direccion_fiscal: Optional[str] = None
    representante_legal: Optional[str] = None
    cargo_representante: Optional[str] = None


class EmpleadoIn(BaseModel):
    nombres: str = Field(min_length=1, max_length=100)
    apellidos: str = Field(min_length=1, max_length=100)
    tipo_documento: str = "DNI"
    documento_identidad: str
    fecha_ingreso: date
    sueldo_basico: float = Field(gt=0)
    puesto: Optional[str] = None
    sede: Optional[str] = None
    sistema_pensionario: str = "ONP"
    afp_nombre: Optional[str] = None
    cuspp: Optional[str] = None
    banco: Optional[str] = None
    numero_cuenta: Optional[str] = None
    asignacion_familiar: bool = False
    cobertura_eps: bool = False
    tipo_contrato: str = "INDEFINIDO"
    fecha_fin_contrato: Optional[date] = None
    area: Optional[str] = None
    centro_costo: Optional[str] = None
    categoria: Optional[str] = "EMPLEADOS"
    calificacion: Optional[str] = None
    tipo_comision: str = "FLUJO"

    @model_validator(mode="after")
    def _validar(self):
        if self.tipo_comision not in ("FLUJO", "MIXTA"):
            raise ValueError("tipo_comision debe ser FLUJO o MIXTA.")
        if self.tipo_contrato not in TIPOS_CONTRATO:
            raise ValueError("tipo_contrato debe ser INDEFINIDO o PLAZO_FIJO.")
        if self.tipo_contrato == "PLAZO_FIJO" and not self.fecha_fin_contrato:
            raise ValueError("Un contrato a plazo fijo necesita fecha de vencimiento.")
        if self.tipo_documento == "DNI" and not re.fullmatch(r"\d{8}", self.documento_identidad):
            raise ValueError("El DNI debe tener 8 dígitos.")
        if self.sistema_pensionario not in ("AFP", "ONP"):
            raise ValueError("sistema_pensionario debe ser AFP u ONP.")
        if self.sistema_pensionario == "AFP" and not self.afp_nombre:
            raise ValueError("Indique la AFP.")
        return self


CLASES_DESPIDO = ("JUSTIFICADO", "ARBITRARIO", "NULO")
TIPOS_CONTRATO = ("INDEFINIDO", "PLAZO_FIJO")


class BajaIn(BaseModel):
    fecha_cese: date
    motivo: str          # código Tabla 17 PLAME
    clase_despido: Optional[str] = None     # solo si el motivo es 02 (Despido)

    @field_validator("clase_despido")
    @classmethod
    def _clase(cls, v):
        if v is not None and v not in CLASES_DESPIDO:
            raise ValueError("clase_despido debe ser JUSTIFICADO, ARBITRARIO o NULO.")
        return v


class SueldoIn(BaseModel):
    monto: float = Field(gt=0)
    fecha_vigencia: date
    motivo: Optional[str] = None


class AusentismoIn(BaseModel):
    codigo_plame: str
    fecha_inicio: date
    fecha_fin: date

    @model_validator(mode="after")
    def _fechas(self):
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio.")
        return self


class VacacionIn(BaseModel):
    fecha_inicio: date
    fecha_fin: date
    dias_vendidos: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _fechas(self):
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin no puede ser anterior a fecha_inicio.")
        return self


class RentaPreviaIn(BaseModel):
    anio: int = Field(ge=2020, le=2100)
    mes: int = Field(ge=1, le=12)
    ruc: str = Field(pattern=r"^\d{11}$")
    razon_social: str = Field(min_length=2, max_length=255)
    ingreso_afecto: float = Field(ge=0)
    retencion: float = Field(ge=0)


class PeriodoIn(BaseModel):
    anio: int = Field(ge=2020, le=2100)
    mes: int = Field(ge=1, le=12)
    fase: str = "Fin de Mes"


class NovedadFila(BaseModel):
    documento: str
    codigo: str
    valor: str           # como texto, para detectar más de 2 decimales (Pre-Check)


class ReaperturaIn(BaseModel):
    motivo: str = Field(min_length=5, max_length=300)


class EmpleadoPatch(BaseModel):
    puesto: Optional[str] = None
    sede: Optional[str] = None
    sistema_pensionario: Optional[str] = None
    afp_nombre: Optional[str] = None
    cuspp: Optional[str] = None
    banco: Optional[str] = None
    numero_cuenta: Optional[str] = None
    asignacion_familiar: Optional[bool] = None
    cobertura_eps: Optional[bool] = None
    tipo_contrato: Optional[str] = None
    fecha_fin_contrato: Optional[date] = None
    clase_despido: Optional[str] = None
    area: Optional[str] = None
    centro_costo: Optional[str] = None
    categoria: Optional[str] = None
    calificacion: Optional[str] = None
    tipo_comision: Optional[str] = None

    @field_validator("tipo_comision")
    @classmethod
    def _tcom(cls, v):
        if v is not None and v not in ("FLUJO", "MIXTA"):
            raise ValueError("tipo_comision debe ser FLUJO o MIXTA.")
        return v

    @field_validator("tipo_contrato")
    @classmethod
    def _tc(cls, v):
        if v is not None and v not in TIPOS_CONTRATO:
            raise ValueError("tipo_contrato debe ser INDEFINIDO o PLAZO_FIJO.")
        return v

    @field_validator("clase_despido")
    @classmethod
    def _cd(cls, v):
        if v is not None and v not in CLASES_DESPIDO:
            raise ValueError("clase_despido debe ser JUSTIFICADO, ARBITRARIO o NULO.")
        return v

    @field_validator("sistema_pensionario")
    @classmethod
    def _sp(cls, v):
        if v is not None and v not in ("AFP", "ONP"):
            raise ValueError("sistema_pensionario debe ser AFP u ONP.")
        return v


class LiquidacionCerrarIn(BaseModel):
    periodo_id: int
    empleado_id: int
    cts_anterior_pagada: Optional[bool] = None


class EmpresaPatch(BaseModel):
    razon_social: Optional[str] = Field(default=None, min_length=2, max_length=255)
    direccion_fiscal: Optional[str] = None
    representante_legal: Optional[str] = None
    cargo_representante: Optional[str] = None


class CtsCalcularIn(BaseModel):
    anio: int = Field(ge=2020, le=2100)
    semestre: str = Field(pattern="^(NOV|MAY)$")      # NOV = mayo-octubre; MAY = noviembre-abril


class CtsDepositarIn(CtsCalcularIn):
    fecha_deposito: date
    empleado_ids: Optional[List[int]] = None            # None = todos los calculados del semestre


class ReingresoIn(BaseModel):
    fecha_ingreso: date
    sueldo_basico: Optional[float] = Field(default=None, gt=0)    # si se indica, se registra el sueldo del nuevo ciclo


class CtsRevertirIn(CtsCalcularIn):
    empleado_ids: Optional[List[int]] = None


class CuentaIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=150)
    plan: str = Field(default="GRATIS", pattern="^(GRATIS|FREELANCER)$")


class CuentaPatch(BaseModel):
    plan: Optional[str] = Field(default=None, pattern="^(GRATIS|FREELANCER)$")
    max_empresas: Optional[int] = Field(default=None, ge=1, le=1000)
    max_trabajadores: Optional[int] = Field(default=None, ge=1, le=100000)
    activa: Optional[bool] = None


class ParametrosPeriodoIn(BaseModel):
    parametros: dict = Field(default_factory=dict)
    afps: List[dict] = Field(default_factory=list)
