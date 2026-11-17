"""Planilla SEMANAL de Construcción Civil (obreros por jornal). Los valores salen del convenio (Diccionario) y de las tarifas
que la empresa editó (cc_valores); el motor no tiene importes escritos en código."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List

from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import reglas as R
from .calculo import D, exigir_abierto, sueldo_a_fecha  # noqa: F401
from .cc import HANDLED_CC, TIPOS_OBRERO, aplicar_overrides
from .diccionario import ErrorDeNegocio, ParametrosPeriodo, admite_carga, cargar_parametros
from .models import Ausentismo, Detalle, Diccionario, Empleado, Empresa, Novedad, Periodo, Resultado, Vacacion

CERO = Decimal("0")


def dias_lab(ini: date, fin: date, desde: date, hasta: date) -> int:
    """Días de lunes a sábado dentro de la intersección de [ini, fin] y [desde, hasta]."""
    a, b, n = max(ini, desde), min(fin, hasta), 0
    while a <= b:
        if a.weekday() != 6:
            n += 1
        a += timedelta(days=1)
    return n


def recalcular_semana(db: Session, periodo: Periodo) -> dict:
    exigir_abierto(periodo)
    dic = db.get(Diccionario, periodo.diccionario_id)
    empresa = db.get(Empresa, periodo.empresa_id)
    pr: ParametrosPeriodo = cargar_parametros(db, periodo.diccionario_id, periodo.fecha_inicio, empresa_id=empresa.id, periodo_id=periodo.id)
    v: Dict[str, Decimal] = dict(pr.p)
    aplicar_overrides(db, empresa.id, periodo.fecha_inicio, v)
    fi, ff = periodo.fecha_inicio, periodo.fecha_fin
    emps: List[Empleado] = (db.query(Empleado).filter(Empleado.empresa_id == empresa.id, Empleado.fecha_ingreso <= ff,
                            or_(Empleado.fecha_cese.is_(None), Empleado.fecha_cese >= fi)).order_by(Empleado.apellidos, Empleado.nombres).all())
    if not emps:
        raise ErrorDeNegocio("No hay obreros vigentes en esta semana.", 409)
    sin_tipo = [f"{e.apellidos}, {e.nombres}" for e in emps if e.tipo_obrero not in TIPOS_OBRERO]
    if sin_tipo:
        raise ErrorDeNegocio("Obreros sin tipo de obrero (operario, oficial, peón…): " + "; ".join(sin_tipo), 422)
    novedades: Dict[int, Dict[str, Decimal]] = defaultdict(dict)
    for n in db.query(Novedad).filter(Novedad.periodo_id == periodo.id):
        c = pr.conceptos.get(n.concepto_codigo)
        if c is None:
            raise ErrorDeNegocio(f"Novedad con concepto inexistente en el Diccionario: {n.concepto_codigo}", 422)
        if not admite_carga(c):
            raise ErrorDeNegocio(f"{n.concepto_codigo} lo calcula el motor; no admite carga manual.", 422)
        novedades[n.empleado_id][n.concepto_codigo] = D(n.valor)
    db.query(Detalle).filter(Detalle.periodo_id == periodo.id).delete(synchronize_session=False)
    db.query(Resultado).filter(Resultado.periodo_id == periodo.id).delete(synchronize_session=False)
    alertas: List[str] = ["Planilla semanal: la retención de 5.ª categoría no se calcula (por validar con su contador)."]
    horas_dia, semanal = D(pr["HORAS_DIA"]), bool(empresa.cc_beneficios_semanales)
    for emp in emps:
        nombre = f"{emp.apellidos}, {emp.nombres}"
        nov = novedades.get(emp.id, {})
        _nom, cat, clave_bae = TIPOS_OBRERO[emp.tipo_obrero]
        jornal, buc_pct = v[f"JORNAL_{cat}"], v[f"BUC_{cat}"]
        filas: List[dict] = []

        def add(cod, monto, formula="", origen="CALCULO"):
            m = R.q2(monto)
            if m != 0:
                filas.append({"codigo": cod, "categoria": pr.conceptos[cod].categoria, "monto": m, "formula": formula, "origen": origen})

        def bolsa(flag):
            return sum((f["monto"] for f in filas if f["categoria"] == "INGRESO" and getattr(pr.conceptos[f["codigo"]], flag)), CERO)

        if "@DIAS_TRABAJADOS" in nov:
            dias = int(nov["@DIAS_TRABAJADOS"])
        else:
            dias = dias_lab(fi, ff, emp.fecha_ingreso, emp.fecha_cese or ff)
            for a in db.query(Ausentismo).filter(Ausentismo.empleado_id == emp.id, Ausentismo.estado == "REGISTRADO", Ausentismo.fecha_inicio <= ff, Ausentismo.fecha_fin >= fi):
                ts = pr.suspensiones.get(a.tipo_suspension_id)
                if ts is not None and ts.concepto_codigo in ("@DIAS_FALTAS", "@DIAS_SUSPENSION", "@DIAS_LIC_SIN_GOCE", "@DIAS_SUBSIDIO", "@DIAS_DMEDICO"):
                    dias -= dias_lab(fi, ff, a.fecha_inicio, a.fecha_fin)
            for x in db.query(Vacacion).filter(Vacacion.empleado_id == emp.id, Vacacion.estado == "REGISTRADO", Vacacion.fecha_inicio <= ff, Vacacion.fecha_fin >= fi):
                dias -= dias_lab(fi, ff, x.fecha_inicio, x.fecha_fin)
            dias = max(dias, 0)
        horas = nov.get("@HORAS_TRABAJADAS", dias * horas_dia)
        fer = nov.get("@DIAS_FERIADO", CERO)
        add("@JORNAL_BASICO", jornal * dias, f"{jornal} × {dias} días")
        add("@DSO", jornal / v["DSO_DIVISOR_HORAS"] * horas, f"{jornal} ÷ {v['DSO_DIVISOR_HORAS']:.0f} × {horas} horas")
        add("@FERIADO", jornal * fer, f"{jornal} × {fer} días de feriado")
        add("@BUC", jornal * buc_pct * dias, f"{jornal} × {buc_pct * 100:.0f} % × {dias} días")
        if clave_bae:
            add("@BAE", jornal * v[clave_bae] * dias, f"{jornal} × {v[clave_bae] * 100:.0f} % × {dias} días")
        add("@MOVILIDAD_CC", v["MOVILIDAD_DIA"] * dias, f"{v['MOVILIDAD_DIA']} × {dias} días")
        vh = jornal / horas_dia
        h60, h100 = nov.get("@HEXT_60", CERO), nov.get("@HEXT_100", CERO)
        if h60:
            add("@HORAS_EXTRAS_60", vh * (1 + v["HE_PCT_60"]) * h60, f"{h60} h × {vh:.4f} × {1 + v['HE_PCT_60']}")
        if h100:
            add("@HORAS_EXTRAS_100", vh * (1 + v["HE_PCT_100"]) * h100, f"{h100} h × {vh:.4f} × {1 + v['HE_PCT_100']}")
        if semanal:                                              # beneficios sociales pagados con la semana
            base_ben = jornal * (dias + fer)
            add("@INDEMNIZACION_CC", (base_ben + vh * (h60 + h100)) * v["INDEM_PCT"], f"{v['INDEM_PCT'] * 100:.0f} % del jornal")
            add("@VACACIONES_TRUNCAS_CC", base_ben * v["VAC_PCT"], f"{v['VAC_PCT'] * 100:.0f} % del jornal")
            div = v["GRATI_DIV_ENE_JUL"] if periodo.fecha_fin.month <= 7 else v["GRATI_DIV_AGO_DIC"]
            if v.get("GRATI_REGLA", CERO) == 1:                 # regla del Excel: +1 día (domingo) solo si las horas superan el umbral
                extra = 1 if horas > v.get("GRATI_UMBRAL_HORAS", D("42.5")) else 0
                grat = v["GRATI_JORNALES"] * jornal / div * (horas / horas_dia + extra)
                add("@GRATIF_CC", grat, f"{v['GRATI_JORNALES']:.0f} jornales ÷ {div:.0f} × ({horas / horas_dia:.2f} días + {extra})")
            else:
                grat = v["GRATI_JORNALES"] * jornal / div * 7 * D(dias) / 6
                add("@GRATIF_CC", grat, f"{v['GRATI_JORNALES']:.0f} jornales ÷ {div:.0f} × 7 días × {dias}/6")
            add("@BONIF_EXTRA_CC", R.q2(grat) * v["BONIF_EXTRA_CC_PCT"], f"{v['BONIF_EXTRA_CC_PCT'] * 100:.0f} % de la gratificación")
        for cod, clave, dclave in (("@BONIF_ALTURA", "ALTURA_PCT", "@DIAS_ALTURA"), ("@BONIF_ALTITUD", "ALTITUD_PCT", "@DIAS_ALTITUD"),
                                   ("@BONIF_NOCTURNA", "NOCTURNA_PCT", "@DIAS_NOCTURNO")):
            pct, dn = v.get(clave, CERO), nov.get(dclave, CERO)
            if pct and dn:
                add(cod, jornal * pct * dn, f"{jornal} × {pct * 100:.2f} % × {dn} días")
        hijos = int(emp.hijos or 0)
        if hijos and "@ASIGNACION_ESCOLAR" not in nov and "@ASIGNACION_ESCOLAR" in pr.conceptos:
            esc = v.get(f"ESCOLAR_{cat}", CERO)
            add("@ASIGNACION_ESCOLAR", esc * hijos * dias, f"{esc} × {hijos} hijo(s) × {dias} días")
        for cod, val in nov.items():                              # novedades de cualquier otro concepto cargable (p. ej. asignación escolar)
            if cod not in HANDLED_CC:
                add(cod, val, "Novedad cargada", origen="NOVEDAD")
        base_prev, base_ess = bolsa("afecta_afp_onp"), bolsa("afecta_essalud")
        if emp.sistema_pensionario == "AFP":
            afp = pr.afps.get((emp.afp_nombre or "").upper())
            if afp is None:
                raise ErrorDeNegocio(f"{nombre}: AFP '{emp.afp_nombre}' no existe en el Diccionario.", 422)
            tope = pr.p.get("RMA_PRIMA", afp.tope_prima)
            com = afp.comision_planilla(emp.tipo_comision)
            ap, co, pri = R.calcular_afp(base_prev, afp.aporte, com, afp.prima, tope)
            add("@FONDO_PENSIONES", ap, f"{base_prev} × {afp.aporte}"); add("@COMISION_VARIABLE", co, f"{base_prev} × {com}"); add("@SEGURO_INVALIDEZ", pri, "prima de seguro")
        else:
            add("@SNP_ONP", R.calcular_onp(base_prev, pr["ONP_TASA"]), f"{base_prev} × {pr['ONP_TASA']}")
        if emp.sistema_pensionario == "AFP" and v.get("AFP_COMPL_TRAB_PCT", CERO):
            add("@AFP_COMPL_TRAB", base_prev * v["AFP_COMPL_TRAB_PCT"], f"{base_prev} × {v['AFP_COMPL_TRAB_PCT'] * 100:.2f} %")
        if emp.sistema_pensionario == "AFP" and v.get("AFP_COMPL_EMP_PCT", CERO):
            add("@AFP_COMPL_EMP", base_prev * v["AFP_COMPL_EMP_PCT"], f"{base_prev} × {v['AFP_COMPL_EMP_PCT'] * 100:.2f} %")
        if "@CUOTA_SINDICAL" not in nov and v.get("CUOTA_SINDICAL_PCT", CERO):
            add("@CUOTA_SINDICAL", jornal * dias * v["CUOTA_SINDICAL_PCT"], f"{v['CUOTA_SINDICAL_PCT'] * 100:.2f} % del jornal básico")
        base_conaf = sum((f["monto"] for f in filas if f["codigo"] in ("@JORNAL_BASICO", "@DSO", "@FERIADO")), CERO)
        add("@CONAFOVICER", base_conaf * v["CONAFOVICER_PCT"], f"{base_conaf} × {v['CONAFOVICER_PCT'] * 100:.0f} %")
        if v.get("FONDO_CAPACIT_PCT", CERO):
            add("@FONDO_CAPACITACION", jornal * dias * v["FONDO_CAPACIT_PCT"], f"{jornal * dias} × {v['FONDO_CAPACIT_PCT'] * 100:.2f} %")
        if v.get("SCTR_PCT", CERO):
            add("@SCTR_CC", base_ess * v["SCTR_PCT"], f"{base_ess} × {v['SCTR_PCT'] * 100:.2f} %")
        ess = R.q2(base_ess * pr["ESSALUD_TASA"]) if base_ess > 0 else CERO          # sin mínimo mensual: la semana no lo alcanza
        add("@ESSALUD", ess, f"base {base_ess}")
        ingresos = sum((f["monto"] for f in filas if f["categoria"] == "INGRESO"), CERO)
        descuentos = sum((f["monto"] for f in filas if f["categoria"] == "DESCUENTO"), CERO)
        aportes = sum((f["monto"] for f in filas if f["categoria"] == "APORTE"), CERO)
        if not emp.numero_cuenta:
            alertas.append(f"{nombre}: sin cuenta de abono.")
        db.add_all([Detalle(periodo_id=periodo.id, empleado_id=emp.id, concepto_codigo=f["codigo"], categoria=f["categoria"], monto=f["monto"],
                            origen=f["origen"], formula=f["formula"]) for f in filas])
        db.add(Resultado(periodo_id=periodo.id, empleado_id=emp.id, remuneracion_computable=ingresos, total_descuentos=descuentos,
                         neto_a_pagar=ingresos - descuentos, aportes_empleador=aportes, base_previsional=base_prev, base_essalud=base_ess,
                         dias_trabajados=dias, horas_extras=h60 + h100,
                         snapshot={"colaborador": nombre, "documento": emp.documento_identidad, "puesto": emp.puesto, "tipo_obrero": emp.tipo_obrero,
                                   "centro_costo": emp.centro_costo, "sistema_pensionario": emp.sistema_pensionario, "afp": emp.afp_nombre,
                                   "banco": emp.banco, "cuenta": emp.numero_cuenta, "jornal": str(jornal), "diccionario": f"{dic.codigo} v{dic.version}"}))
    db.commit()
    return {"colaboradores": len(emps), "alertas": alertas, "bloqueados_por_liquidacion": 0}
