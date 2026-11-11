"""Carga masiva de trabajadores desde Excel.

Reglas (decididas por el dueño):
- TODO O NADA: si una sola fila tiene error no se guarda ninguna.
- Un trabajador que ya existe (mismo tipo y número de documento) se ACTUALIZA con los datos del Excel; una celda en blanco no borra lo que ya tiene.
- El sistema pensionario es obligatorio en todas las filas.
- Los datos opcionales en blanco no impiden la carga: se aplican valores por defecto y, si falta algo importante (CUSPP, cuenta), solo se avisa.
- Un cambio de sueldo de un trabajador existente necesita la fecha de vigencia del nuevo sueldo (el historial de sueldos es por fecha)."""
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.utils.datetime import from_excel
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.properties import PageSetupProperties

from fastapi import HTTPException

from .diccionario import ErrorDeNegocio
from .liquidacion import exigir_sin_liquidacion_cerrada
from .models import DiccionarioAFP, Empleado, Sueldo

MAX_FILAS = 500
TIPOS_DOC = ("DNI", "CE", "PASAPORTE", "OTRO")
O, C, P = "OBLIGATORIO", "CONDICIONAL", "OPCIONAL"
# (clave interna, encabezado, tipo de dato, nivel, formato / valores permitidos, si queda en blanco, ejemplo, observación)
COLS = [
    ("apellidos", "Apellidos", "Texto (hasta 100)", O, "Apellido paterno y materno. Se guarda en MAYÚSCULAS.", "No se puede: se rechaza.", "RAMOS TORRES", ""),
    ("nombres", "Nombres", "Texto (hasta 100)", O, "Se guarda en MAYÚSCULAS.", "No se puede: se rechaza.", "LUIS ALBERTO", ""),
    ("tipo_documento", "Tipo de documento", "Lista", P, "DNI · CE · PASAPORTE · OTRO", "Se asume DNI.", "DNI", "Con DNI, el número debe tener 8 dígitos."),
    ("documento", "N° de documento", "Texto", O, "DNI: 8 dígitos. Otros: texto libre. Escríbalo como TEXTO.", "No se puede: se rechaza.", "45123789", "Identifica al trabajador: si ya existe en la empresa, se ACTUALIZA."),
    ("fecha_ingreso", "Fecha de ingreso", "Fecha", O, "dd/mm/aaaa", "No se puede: se rechaza.", "03/02/2025", "En un trabajador que ya existe no se modifica."),
    ("sueldo", "Sueldo básico (S/)", "Número", O, "Mayor que 0, hasta 2 decimales. Sin «S/» ni separador de miles.", "No se puede: se rechaza.", "3500.00", "Si el trabajador ya existe y el sueldo cambia, indique la vigencia del nuevo sueldo."),
    ("vigencia_sueldo", "Vigencia del nuevo sueldo", "Fecha", C, "dd/mm/aaaa", "Obligatoria solo si el trabajador ya existe y su sueldo cambia.", "", "En trabajadores nuevos se ignora: el sueldo rige desde el ingreso."),
    ("sistema_pensionario", "Sistema pensionario", "Lista", O, "AFP · ONP", "No se puede: se rechaza (no se asume para no calcular mal los descuentos).", "AFP", ""),
    ("afp", "AFP", "Lista", C, "Una de las AFP del Diccionario (HABITAT · INTEGRA · PRIMA · PROFUTURO).", "Obligatoria si el sistema es AFP y el trabajador es nuevo o cambia a AFP; con ONP se ignora.", "PRIMA", ""),
    ("cuspp", "CUSPP", "Texto (12)", P, "Código único del afiliado al SPP.", "Se carga con AVISO: el AFPnet saldrá incompleto hasta completarlo.", "589741ARTRM3", "Solo AFP."),
    ("tipo_comision", "Tipo de comisión AFP", "Lista", P, "FLUJO · MIXTA", "Nuevo: FLUJO. Existente: no cambia.", "FLUJO", "Solo AFP."),
    ("asignacion_familiar", "Asignación familiar", "Lista", P, "SI · NO", "Nuevo: NO. Existente: no cambia.", "SI", "Con SI se paga el 10 % de la RMV vigente."),
    ("cobertura_eps", "Cobertura EPS", "Lista", P, "SI · NO", "Nuevo: NO (EsSalud 9 %). Existente: no cambia.", "NO", ""),
    ("tipo_contrato", "Tipo de contrato", "Lista", P, "INDEFINIDO · PLAZO_FIJO", "Nuevo: INDEFINIDO. Existente: no cambia.", "INDEFINIDO", ""),
    ("fecha_fin_contrato", "Fecha de vencimiento del contrato", "Fecha", C, "dd/mm/aaaa", "Obligatoria si el contrato es PLAZO_FIJO (y el trabajador no la tiene ya).", "", ""),
    ("puesto", "Puesto", "Texto", P, "Se guarda en MAYÚSCULAS.", "Nuevo: sin puesto. Existente: no cambia.", "ANALISTA CONTABLE", ""),
    ("sede", "Sede", "Texto", P, "Por ejemplo: Lima Principal · Magdalena del Mar.", "Nuevo: sin sede. Existente: no cambia.", "MAGDALENA DEL MAR", ""),
    ("area", "Área", "Texto", P, "Nombre del área.", "Nuevo: sin área. Existente: no cambia.", "ADMINISTRACION Y GH", "Si la empresa registró el catálogo de áreas, Cálculo avisa a quienes no coincidan."),
    ("centro_costo", "Centro de costo", "Texto", P, "Código y nombre, por ejemplo 000001101 ADMINISTRATIVO.", "Nuevo: sin centro de costo. Existente: no cambia.", "000001101 ADMINISTRATIVO", "Igual que el área."),
    ("categoria", "Categoría", "Texto", P, "Categoría de planilla.", "Nuevo: EMPLEADOS. Existente: no cambia.", "EMPLEADOS", ""),
    ("calificacion", "Calificación", "Texto", P, "Por ejemplo: CONFIANZA - NO FISCAL.", "Nuevo: sin calificación. Existente: no cambia.", "", ""),
    ("banco", "Banco", "Lista", P, "BCP · BBVA · INTERBANK · SCOTIABANK (se admite otro texto).", "Nuevo: sin banco. Existente: no cambia.", "BCP", ""),
    ("numero_cuenta", "N° de cuenta", "Texto", P, "Cuenta de haberes, sin espacios. Escríbala como TEXTO.", "Se carga con AVISO: Cálculo alertará «sin cuenta de abono».", "19412345678012", ""),
]
CLAVE_COL = {c[0]: c for c in COLS}
OBLIGATORIAS = [c[0] for c in COLS if c[3] == O]
MAX_LARGO = {"apellidos": 100, "nombres": 100, "puesto": 150, "sede": 100, "area": 100, "centro_costo": 100, "categoria": 30, "calificacion": 60, "banco": 60, "numero_cuenta": 50, "cuspp": 15, "documento": 15}
ETIQ = {k: v[1] for k, v in CLAVE_COL.items()}


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", s.replace("*", ""))


NORM_A_CLAVE = {_norm(c[1]): c[0] for c in COLS}
NORM_A_CLAVE.update({"NRODEDOCUMENTO": "documento", "NUMERODEDOCUMENTO": "documento", "SUELDOBASICO": "sueldo", "SUELDO": "sueldo",
                     "VIGENCIADELNUEVOSUELDOSOLOSIELTRABAJADORYAEXISTE": "vigencia_sueldo", "AREA": "area", "NDECUENTA": "numero_cuenta", "NUMERODECUENTA": "numero_cuenta"})


# ----------------------------------------------------------------------------- conversión de celdas
def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (datetime, date)):
        return v.strftime("%d/%m/%Y")
    return re.sub(r"\s+", " ", str(v)).strip()


def _fecha(v):
    """Devuelve (date|None, error|None). Acepta fechas de Excel, dd/mm/aaaa, aaaa-mm-dd y dd-mm-aaaa."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None
    if isinstance(v, datetime):
        return v.date(), None
    if isinstance(v, date):
        return v, None
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:
        try:
            return from_excel(v).date(), None
        except Exception:
            pass
    s = str(v).strip()
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, f).date(), None
        except ValueError:
            continue
    return None, f"fecha no válida «{s}» (use dd/mm/aaaa)"


def _numero(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None
    s = re.sub(r"(?i)s/\.?|\s", "", str(v))
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return Decimal(s), None
    except InvalidOperation:
        return None, f"número no válido «{v}»"


def _si_no(v):
    """None = sin dato (celda en blanco)."""
    s = _texto(v).upper()
    if s == "":
        return None, None
    if s in ("SI", "SÍ", "S", "1", "X", "TRUE", "VERDADERO"):
        return True, None
    if s in ("NO", "N", "0", "FALSE", "FALSO"):
        return False, None
    return None, f"«{v}» no es SI ni NO"


# ----------------------------------------------------------------------------- lectura del Excel
def leer_excel(contenido: bytes) -> list:
    try:
        wb = load_workbook(io.BytesIO(contenido), data_only=True)
    except Exception:
        raise ErrorDeNegocio("El archivo no es un Excel (.xlsx) válido.")
    hoja = next((wb[n] for n in wb.sheetnames if _norm(n) == "TRABAJADORES"), None)
    if hoja is None:
        raise ErrorDeNegocio("No se encontró la hoja «Trabajadores». Use la plantilla descargada desde el sistema.")
    enc = {}
    for j, c in enumerate(next(hoja.iter_rows(min_row=1, max_row=1)), 1):
        k = NORM_A_CLAVE.get(_norm(c.value))
        if k and k not in enc:
            enc[k] = j
    faltan = [ETIQ[k] for k in OBLIGATORIAS if k not in enc]
    if faltan:
        raise ErrorDeNegocio("Faltan columnas obligatorias en la hoja «Trabajadores»: " + ", ".join(faltan) + ". No cambie los encabezados de la plantilla.")
    filas = []
    for r in range(2, hoja.max_row + 1):
        valores = {k: hoja.cell(r, j).value for k, j in enc.items()}
        if all(_texto(v) == "" for v in valores.values()):
            continue
        if _texto(valores.get("apellidos")).upper().startswith("EJEMPLO"):
            continue
        valores["_fila"] = r
        filas.append(valores)
    if not filas:
        raise ErrorDeNegocio("El archivo no tiene trabajadores para cargar (desde la fila 3 de la hoja «Trabajadores»).")
    if len(filas) > MAX_FILAS:
        raise ErrorDeNegocio(f"El archivo tiene {len(filas)} filas: el máximo es {MAX_FILAS} por carga.")
    return filas


# ----------------------------------------------------------------------------- análisis (no escribe nada)
def analizar(db, empresa, cuenta, filas: list):
    afps = {a.nombre.upper() for a in db.query(DiccionarioAFP).filter(DiccionarioAFP.diccionario_id == empresa.diccionario_id)}
    existentes = {(e.tipo_documento, e.documento_identidad): e for e in db.query(Empleado).filter(Empleado.empresa_id == empresa.id)}
    activos = sum(1 for e in existentes.values() if e.fecha_cese is None)
    hoy = date.today()
    informe, ops, vistos, nuevos = [], [], {}, 0
    for v in filas:
        err, avi = [], []
        txt = lambda k: _texto(v.get(k))
        tipo_doc = (txt("tipo_documento") or "DNI").upper()
        if tipo_doc not in TIPOS_DOC:
            err.append(f"Tipo de documento «{tipo_doc}» no válido (use {', '.join(TIPOS_DOC)}).")
        doc = txt("documento")
        if tipo_doc == "DNI" and re.fullmatch(r"\d{1,7}", doc):
            doc = doc.zfill(8); avi.append("El DNI tenía menos de 8 dígitos: se completó con ceros a la izquierda.")
        if not doc:
            err.append("Falta el N° de documento.")
        elif tipo_doc == "DNI" and not re.fullmatch(r"\d{8}", doc):
            err.append("El DNI debe tener 8 dígitos.")
        elif len(doc) > 15:
            err.append("El N° de documento no puede pasar de 15 caracteres.")
        apellidos, nombres = txt("apellidos").upper(), txt("nombres").upper()
        for k, val in (("apellidos", apellidos), ("nombres", nombres)):
            if not val:
                err.append(f"Falta {ETIQ[k].lower()}.")
            elif len(val) > 100:
                err.append(f"{ETIQ[k]} pasa de 100 caracteres.")
        ingreso, e1 = _fecha(v.get("fecha_ingreso"))
        if e1: err.append("Fecha de ingreso: " + e1)
        elif ingreso is None: err.append("Falta la fecha de ingreso.")
        sueldo, e2 = _numero(v.get("sueldo"))
        if e2: err.append("Sueldo: " + e2)
        elif sueldo is None: err.append("Falta el sueldo básico.")
        elif sueldo <= 0: err.append("El sueldo debe ser mayor que cero.")
        else: sueldo = sueldo.quantize(Decimal("0.01"))
        sp = txt("sistema_pensionario").upper()
        if sp not in ("AFP", "ONP"):
            err.append("Falta el sistema pensionario (AFP u ONP)." if not sp else f"Sistema pensionario «{sp}» no válido (use AFP u ONP).")
        afp = txt("afp").upper()
        if sp == "AFP" and afp and afp not in afps:
            err.append(f"AFP «{afp}» no reconocida. Opciones: {', '.join(sorted(afps))}.")
        tc = txt("tipo_comision").upper()
        if tc and tc not in ("FLUJO", "MIXTA"):
            err.append("Tipo de comisión debe ser FLUJO o MIXTA.")
        asig, e3 = _si_no(v.get("asignacion_familiar")); eps, e4 = _si_no(v.get("cobertura_eps"))
        if e3: err.append("Asignación familiar: " + e3)
        if e4: err.append("Cobertura EPS: " + e4)
        contrato = txt("tipo_contrato").upper()
        if contrato and contrato not in ("INDEFINIDO", "PLAZO_FIJO"):
            err.append("Tipo de contrato debe ser INDEFINIDO o PLAZO_FIJO.")
        fin, e5 = _fecha(v.get("fecha_fin_contrato"))
        if e5: err.append("Fecha de vencimiento: " + e5)
        vig, e6 = _fecha(v.get("vigencia_sueldo"))
        if e6: err.append("Vigencia del nuevo sueldo: " + e6)
        for k in ("puesto", "sede", "area", "centro_costo", "categoria", "calificacion", "banco", "numero_cuenta", "cuspp"):
            if len(txt(k)) > MAX_LARGO[k]:
                err.append(f"{ETIQ[k]} pasa de {MAX_LARGO[k]} caracteres.")
        clave = (tipo_doc, doc)
        if clave in vistos:
            err.append(f"Documento repetido: ya aparece en la fila {vistos[clave]}.")
        else:
            vistos[clave] = v["_fila"]
        emp = existentes.get(clave)
        fila = {"fila": v["_fila"], "tipo_documento": tipo_doc, "documento": doc, "colaborador": f"{apellidos}, {nombres}".strip(", "),
                "accion": "ACTUALIZAR" if emp else "CREAR", "errores": err, "avisos": avi, "cambios": []}
        op = None
        if emp is None and not err:
            nuevos += 1
            if activos + nuevos > cuenta.max_trabajadores:
                err.append(f"Excede el límite de su plan: {cuenta.max_trabajadores} trabajadores activos (hoy {activos}).")
            if sp == "AFP" and not afp: err.append("Indique la AFP (el sistema pensionario es AFP).")
            if contrato == "PLAZO_FIJO" and not fin: err.append("Un contrato a plazo fijo necesita fecha de vencimiento.")
            if fin and ingreso and fin < ingreso: err.append("La fecha de vencimiento es anterior al ingreso.")
            if not err:
                if sp == "AFP" and not txt("cuspp"): avi.append("Sin CUSPP: el archivo AFPnet saldrá incompleto hasta completarlo.")
                if not txt("numero_cuenta"): avi.append("Sin N° de cuenta: no podrá incluirse en la remesa de pagos.")
                op = ("CREAR", dict(apellidos=apellidos, nombres=nombres, tipo_documento=tipo_doc, documento_identidad=doc, fecha_ingreso=ingreso, sueldo=sueldo,
                                    sistema_pensionario=sp, afp_nombre=afp if sp == "AFP" else None, cuspp=txt("cuspp") or None,
                                    tipo_comision=(tc or "FLUJO") if sp == "AFP" else "FLUJO", asignacion_familiar=bool(asig), cobertura_eps=bool(eps),
                                    tipo_contrato=contrato or "INDEFINIDO", fecha_fin_contrato=fin if (contrato or "INDEFINIDO") == "PLAZO_FIJO" else None,
                                    puesto=txt("puesto").upper() or None, sede=txt("sede") or None, area=txt("area") or None,
                                    centro_costo=txt("centro_costo") or None, categoria=txt("categoria") or "EMPLEADOS", calificacion=txt("calificacion") or None,
                                    banco=txt("banco") or None, numero_cuenta=txt("numero_cuenta") or None))
        elif emp is not None and not err:
            cambios, setv = [], {}
            def cambia(campo, etiqueta, nuevo, actual=None):
                actual = getattr(emp, campo) if actual is None else actual
                if nuevo is not None and nuevo != actual:
                    cambios.append({"campo": etiqueta, "antes": "" if actual is None else str(actual), "despues": str(nuevo)}); setv[campo] = nuevo
            if apellidos != emp.apellidos or nombres != emp.nombres:
                avi.append("El nombre del Excel es distinto al del legajo: no se modifica por carga masiva (edítelo en el Legajo).")
            if ingreso and ingreso != emp.fecha_ingreso:
                avi.append("La fecha de ingreso del Excel es distinta a la del legajo: no se modifica por carga masiva.")
            if emp.fecha_cese is not None:
                avi.append("El colaborador está cesado: se actualizan sus datos de legajo.")
            sp_nuevo = sp or emp.sistema_pensionario
            if sp_nuevo != emp.sistema_pensionario:
                cambia("sistema_pensionario", "Sistema pensionario", sp_nuevo)
                if sp_nuevo == "ONP":
                    setv["afp_nombre"] = None; cambios.append({"campo": "AFP", "antes": emp.afp_nombre or "", "despues": ""})
            if sp_nuevo == "AFP":
                if afp: cambia("afp_nombre", "AFP", afp)
                elif not emp.afp_nombre: err.append("Indique la AFP (el sistema pensionario es AFP).")
            if tc and sp_nuevo == "AFP": cambia("tipo_comision", "Tipo de comisión AFP", tc)
            if txt("cuspp"): cambia("cuspp", "CUSPP", txt("cuspp"))
            elif sp_nuevo == "AFP" and not emp.cuspp: avi.append("Sin CUSPP: el archivo AFPnet saldrá incompleto hasta completarlo.")
            if asig is not None: cambia("asignacion_familiar", "Asignación familiar", asig)
            if eps is not None: cambia("cobertura_eps", "Cobertura EPS", eps)
            contrato_n = contrato or emp.tipo_contrato
            if contrato: cambia("tipo_contrato", "Tipo de contrato", contrato)
            if contrato_n == "PLAZO_FIJO":
                fin_n = fin or emp.fecha_fin_contrato
                if not fin_n: err.append("Un contrato a plazo fijo necesita fecha de vencimiento.")
                elif fin: cambia("fecha_fin_contrato", "Vencimiento del contrato", fin)
            elif contrato == "INDEFINIDO" and emp.fecha_fin_contrato:
                setv["fecha_fin_contrato"] = None; cambios.append({"campo": "Vencimiento del contrato", "antes": str(emp.fecha_fin_contrato), "despues": ""})
            for k, et, up in (("puesto", "Puesto", True), ("sede", "Sede", False), ("area", "Área", False), ("centro_costo", "Centro de costo", False),
                              ("categoria", "Categoría", False), ("calificacion", "Calificación", False), ("banco", "Banco", False), ("numero_cuenta", "N° de cuenta", False)):
                if txt(k): cambia(k, et, txt(k).upper() if up else txt(k))
            if not txt("numero_cuenta") and not emp.numero_cuenta: avi.append("Sin N° de cuenta: no podrá incluirse en la remesa de pagos.")
            sueldo_op = None
            actual = (db.query(Sueldo).filter(Sueldo.empleado_id == emp.id, Sueldo.fecha_vigencia <= hoy).order_by(Sueldo.fecha_vigencia.desc()).first())
            if sueldo is not None and actual is not None and Decimal(str(actual.monto)) != sueldo:
                if vig is None:
                    err.append(f"El sueldo cambia de {actual.monto} a {sueldo}: indique la «Vigencia del nuevo sueldo».")
                elif vig < emp.fecha_ingreso:
                    err.append("La vigencia del nuevo sueldo no puede ser anterior al ingreso.")
                elif db.query(Sueldo).filter(Sueldo.empleado_id == emp.id, Sueldo.fecha_vigencia == vig).first():
                    err.append("Ya existe un sueldo con esa fecha de vigencia.")
                else:
                    try:
                        exigir_sin_liquidacion_cerrada(db, emp.id, "registrar sueldos")
                    except HTTPException as ex:
                        err.append(str(ex.detail))
                    sueldo_op = (vig, sueldo); cambios.append({"campo": "Sueldo (desde " + vig.strftime("%d/%m/%Y") + ")", "antes": str(actual.monto), "despues": str(sueldo)})
            if not cambios and not err:
                avi.append("Sin cambios: los datos del Excel coinciden con el legajo.")
            fila["cambios"] = cambios
            if not err:
                op = ("ACTUALIZAR", emp, setv, sueldo_op)
        fila["estado"] = "ERROR" if err else ("AVISO" if avi else "OK")
        informe.append(fila)
        if op:
            ops.append(op)
    resumen = {"total": len(informe), "crear": sum(1 for f in informe if f["accion"] == "CREAR" and not f["errores"]),
               "actualizar": sum(1 for f in informe if f["accion"] == "ACTUALIZAR" and not f["errores"]),
               "con_aviso": sum(1 for f in informe if f["estado"] == "AVISO"), "con_error": sum(1 for f in informe if f["estado"] == "ERROR"),
               "activos_hoy": activos, "limite_plan": cuenta.max_trabajadores}
    return {"resumen": resumen, "filas": informe, "puede_cargar": resumen["con_error"] == 0 and resumen["total"] > 0}, ops


# ----------------------------------------------------------------------------- aplicación (todo o nada)
def aplicar(db, empresa, ops: list) -> dict:
    creados = actualizados = 0
    try:
        for op in ops:
            if op[0] == "CREAR":
                d = op[1]
                e = Empleado(empresa_id=empresa.id, nombres=d["nombres"], apellidos=d["apellidos"], tipo_documento=d["tipo_documento"],
                             documento_identidad=d["documento_identidad"], puesto=d["puesto"], sede=d["sede"], fecha_ingreso=d["fecha_ingreso"],
                             sistema_pensionario=d["sistema_pensionario"], afp_nombre=d["afp_nombre"], cuspp=d["cuspp"], banco=d["banco"],
                             numero_cuenta=d["numero_cuenta"], asignacion_familiar=d["asignacion_familiar"], cobertura_eps=d["cobertura_eps"],
                             regimen_codigo="RG_728", tipo_contrato=d["tipo_contrato"], fecha_fin_contrato=d["fecha_fin_contrato"], area=d["area"],
                             centro_costo=d["centro_costo"], categoria=d["categoria"], calificacion=d["calificacion"], tipo_comision=d["tipo_comision"])
                db.add(e); db.flush()
                db.add(Sueldo(empleado_id=e.id, monto=d["sueldo"], fecha_vigencia=d["fecha_ingreso"], motivo="Sueldo inicial de contrato"))
                creados += 1
            else:
                _, emp, setv, sueldo_op = op
                for k, val in setv.items():
                    setattr(emp, k, val)
                if sueldo_op:
                    db.add(Sueldo(empleado_id=emp.id, monto=sueldo_op[1], fecha_vigencia=sueldo_op[0], motivo="Actualización por carga masiva"))
                if setv or sueldo_op:
                    actualizados += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"creados": creados, "actualizados": actualizados}


# ----------------------------------------------------------------------------- plantilla
def plantilla(afps: list) -> bytes:
    AZUL, AZUL2, GRIS, BLANCO = "1F4FBF", "5B8DEF", "64748B", "FFFFFF"
    F = lambda **k: Font(name="Arial", size=k.pop("size", 10), **k)
    fill = lambda c: PatternFill("solid", start_color=c, end_color=c)
    borde = Border(*(Side(style="thin", color="CBD5E1"),) * 4)
    envolver = Alignment(wrap_text=True, vertical="top")
    color = {O: AZUL, C: AZUL2, P: GRIS}
    wb = Workbook()
    w = wb.active; w.title = "Léeme"; w.sheet_view.showGridLines = False
    w.column_dimensions["A"].width = 4; w.column_dimensions["B"].width = 26; w.column_dimensions["C"].width = 92
    w["B2"] = "Planilla Ágil · Plantilla de carga masiva de trabajadores"; w["B2"].font = F(size=15, bold=True, color=AZUL)
    w["B3"] = "Los trabajadores nuevos se crean y los que ya existen (mismo documento) se actualizan. Todo o nada: con un solo error no se carga ninguno."; w["B3"].font = F(italic=True, color=GRIS)
    w["B5"] = "CÓMO USARLA"; w["B5"].font = F(bold=True, color=AZUL)
    pasos = ["Llene la hoja «Trabajadores»: una fila por trabajador, desde la fila 3. La fila 2 es un ejemplo y se ignora (puede borrarla).",
             "Encabezado azul oscuro = obligatorio. Azul medio = obligatorio solo en ciertos casos. Gris = opcional: puede quedar en blanco.",
             "No cambie los nombres de los encabezados. Use las listas desplegables. Escriba N° de documento y N° de cuenta como TEXTO; las fechas como dd/mm/aaaa.",
             "En Planilla Ágil: Legajo de Personal → Carga masiva → suba el archivo. Se muestra una revisión fila por fila y solo se guarda al confirmar.",
             "Si un trabajador ya existe, se actualizan solo las celdas que llene: una celda en blanco no borra lo que ya tiene. Nombre e ingreso no se modifican.",
             "Si cambia el sueldo de un trabajador que ya existe, llene «Vigencia del nuevo sueldo» (el historial de sueldos es por fecha)."]
    for i, t in enumerate(pasos, 1):
        w.cell(5 + i, 2, f"{i}.").font = F(bold=True); w.cell(5 + i, 2).alignment = Alignment(vertical="top", horizontal="right")
        w.cell(5 + i, 3, t).font = F(); w.cell(5 + i, 3).alignment = envolver
    w["B13"] = "COLORES DE ENCABEZADO"; w["B13"].font = F(bold=True, color=AZUL)
    for i, (n, t) in enumerate([(O, "Debe venir lleno. Si falta, la carga se detiene."), (C, "Obligatoria solo en ciertos casos."), (P, "Puede quedar en blanco; se aplica el valor por defecto o no se cambia lo existente.")]):
        r = 14 + i; w.cell(r, 2, n).font = F(bold=True, color=BLANCO); w.cell(r, 2).fill = fill(color[n]); w.cell(r, 3, t).font = F()
    t = wb.create_sheet("Trabajadores"); t.freeze_panes = "C3"
    for j, (k, nom, tipo, niv, fmt, blanco, ej, obs) in enumerate(COLS, 1):
        c = t.cell(1, j, nom + (" *" if niv == O else "")); c.font = F(bold=True, color=BLANCO); c.fill = fill(color[niv])
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center"); c.border = borde
        c.comment = Comment(f"{niv}. {fmt}\nSi queda en blanco: {blanco}", "Planilla Ágil")
        e = t.cell(2, j, ej or None); e.font = F(italic=True, color=GRIS); e.fill = fill("F1F5F9"); e.border = borde
        t.column_dimensions[L(j)].width = max(14, min(30, len(nom) + 4))
    t.row_dimensions[1].height = 44
    idx = {c[0]: i + 1 for i, c in enumerate(COLS)}
    t.cell(2, 1).value = "EJEMPLO RAMOS TORRES"; t.cell(2, idx["fecha_ingreso"]).value = date(2025, 2, 3); t.cell(2, idx["sueldo"]).value = 3500
    for k, f_ in {"documento": "@", "numero_cuenta": "@", "cuspp": "@", "fecha_ingreso": "dd/mm/yyyy", "fecha_fin_contrato": "dd/mm/yyyy", "vigencia_sueldo": "dd/mm/yyyy", "sueldo": "#,##0.00"}.items():
        for r in range(2, MAX_FILAS + 2): t.cell(r, idx[k]).number_format = f_
    def lista(k, formula, estricta=True):
        dv = DataValidation(type="list", formula1=formula, allow_blank=True, showErrorMessage=True, errorStyle="stop" if estricta else "warning", errorTitle="Valor no válido", error="Elija un valor de la lista.")
        t.add_data_validation(dv); col = L(idx[k]); dv.add(f"{col}3:{col}{MAX_FILAS + 1}")
    n_afp = max(1, len(afps))
    lista("tipo_documento", "Listas!$A$2:$A$5"); lista("sistema_pensionario", "Listas!$B$2:$B$3"); lista("afp", f"Listas!$C$2:$C${n_afp + 1}")
    lista("tipo_comision", "Listas!$D$2:$D$3"); lista("asignacion_familiar", "Listas!$E$2:$E$3"); lista("cobertura_eps", "Listas!$E$2:$E$3")
    lista("tipo_contrato", "Listas!$F$2:$F$3"); lista("banco", "Listas!$G$2:$G$5", estricta=False)
    p = wb.create_sheet("Parámetros"); p.sheet_view.showGridLines = False
    for j, (e, a) in enumerate(zip(["N.º", "Columna", "Tipo de dato", "Obligatoriedad", "Formato / valores permitidos", "Si queda en blanco", "Ejemplo", "Observación"], [6, 30, 18, 16, 54, 52, 24, 54]), 1):
        c = p.cell(1, j, e); c.font = F(bold=True, color=BLANCO); c.fill = fill(AZUL); c.alignment = Alignment(wrap_text=True, vertical="center"); c.border = borde; p.column_dimensions[L(j)].width = a
    for i, (k, nom, tipo, niv, fmt, blanco, ej, obs) in enumerate(COLS, 1):
        for j, v in enumerate([i, nom, tipo, niv, fmt, blanco, ej, obs], 1):
            c = p.cell(1 + i, j, v); c.font = F(); c.alignment = envolver; c.border = borde
        n = p.cell(1 + i, 4); n.font = F(bold=True, color=BLANCO); n.fill = fill(color[niv]); n.alignment = Alignment(horizontal="center", vertical="top")
    r0 = len(COLS) + 4; p.cell(r0, 2, "REGLAS DE LA CARGA").font = F(bold=True, color=AZUL, size=11)
    reglas = [("Todo o nada", "Si una sola fila tiene error no se guarda ninguna. El sistema muestra cada error con su número de fila para corregir y volver a subir."),
              ("Trabajador ya existente", "Si el tipo y número de documento ya existen en la empresa, se ACTUALIZAN sus datos con los del Excel. Las celdas en blanco no borran datos. Nombre e ingreso no se modifican."),
              ("Cambio de sueldo", "Si el sueldo cambia en un trabajador existente se exige la «Vigencia del nuevo sueldo»; se agrega al historial de sueldos."),
              ("Duplicados en el archivo", "Un documento repetido en el archivo es un error."),
              ("Límite del plan", "Los trabajadores activos (existentes más nuevos) no pueden superar el límite del plan."),
              ("Mayúsculas y espacios", "Apellidos, nombres y puesto se guardan en MAYÚSCULAS. Se eliminan espacios sobrantes."),
              ("Sí / No", "Se aceptan SI, S, 1, X como sí; NO, N, 0 como no. En blanco: nuevo = NO; existente = no cambia."),
              ("Fechas y números", "Fechas dd/mm/aaaa (también fechas de Excel). Sueldo con punto o coma decimal. DNI con menos de 8 dígitos se completa con ceros y se avisa."),
              ("Régimen", "Todos se cargan en Régimen General (D.Leg. 728)."), ("Tamaño", f"Hasta {MAX_FILAS} filas por archivo.")]
    for i, (a, b) in enumerate(reglas, 1):
        p.cell(r0 + i, 2, a).font = F(bold=True); p.cell(r0 + i, 2).alignment = envolver
        p.cell(r0 + i, 5, b).font = F(); p.cell(r0 + i, 5).alignment = envolver; p.merge_cells(start_row=r0 + i, start_column=5, end_row=r0 + i, end_column=8); p.row_dimensions[r0 + i].height = 30
    p.freeze_panes = "C2"
    ls = wb.create_sheet("Listas")
    listas = {"Tipo de documento": list(TIPOS_DOC), "Sistema pensionario": ["AFP", "ONP"], "AFP": sorted(afps), "Tipo de comisión": ["FLUJO", "MIXTA"], "SI / NO": ["SI", "NO"],
              "Tipo de contrato": ["INDEFINIDO", "PLAZO_FIJO"], "Banco": ["BCP", "BBVA", "INTERBANK", "SCOTIABANK"]}
    for j, (k, vs) in enumerate(listas.items(), 1):
        c = ls.cell(1, j, k); c.font = F(bold=True, color=BLANCO); c.fill = fill(AZUL); ls.column_dimensions[L(j)].width = 22
        for i, v in enumerate(vs, 2): ls.cell(i, j, v).font = F()
    for ws in wb.worksheets:
        ws.page_setup.orientation = "landscape"; ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()
