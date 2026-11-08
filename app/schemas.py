import re
from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

MONEY = re.compile(r"^-?\d+(\.\d{1,2})?$")


class EmpresaIn(BaseModel):
    ruc: str = Field(pattern=r"^\d{11}$")
    razon_social: str = Field(min_length=2, max_length=255)
    diccionario_codigo: str = "RG_728"


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

    @model_validator(mode="after")
    def _validar(self):
        if self.tipo_documento == "DNI" and not re.fullmatch(r"\d{8}", self.documento_identidad):
            raise ValueError("El DNI debe tener 8 dígitos.")
        if self.sistema_pensionario not in ("AFP", "ONP"):
            raise ValueError("sistema_pensionario debe ser AFP u ONP.")
        if self.sistema_pensionario == "AFP" and not self.afp_nombre:
            raise ValueError("Indique la AFP.")
        return self


class BajaIn(BaseModel):
    fecha_cese: date
    motivo: str          # código Tabla 17 PLAME


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
    anio: int
    mes: int = Field(ge=1, le=12)
    ruc: str = Field(pattern=r"^\d{11}$")
    razon_social: str
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

    @field_validator("sistema_pensionario")
    @classmethod
    def _sp(cls, v):
        if v is not None and v not in ("AFP", "ONP"):
            raise ValueError("sistema_pensionario debe ser AFP u ONP.")
        return v
