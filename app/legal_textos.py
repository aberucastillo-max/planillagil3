"""Textos legales públicos. BORRADOR: debe validarlo un abogado antes de usarse con clientes. Versión = fecha de la última edición."""
import os

VERSION = "2026-11-19"
NOMBRE = "Planilla Ágil"


def contacto() -> str:
    return os.getenv("LEGAL_CONTACTO", "[correo de contacto por definir]")


TITULOS = {"terminos": "Términos de uso", "privacidad": "Política de privacidad", "encargo": "Acuerdo de encargo de tratamiento y confidencialidad"}

TERMINOS = [
    ("1. Servicio", "Planilla Ágil es un sistema en línea para calcular planillas, boletas y liquidaciones en el Perú. El cliente (la empresa) es responsable de los datos que ingresa y de las decisiones laborales y tributarias que tome con los resultados."),
    ("2. Cuenta y usuarios", "Cada cuenta tiene un propietario que crea y administra a sus usuarios. Las credenciales son personales; el cliente responde por el uso de sus usuarios y debe avisar de inmediato cualquier acceso no autorizado."),
    ("3. Planes y pagos", "Los precios se muestran sin IGV. Al vencer el plan hay un periodo de gracia; luego la cuenta pasa a solo lectura: la información se conserva y puede consultarse y descargarse, pero no se calcula ni se edita hasta renovar."),
    ("4. Uso permitido", "No se permite acceder a datos de otros clientes, intentar vulnerar la seguridad, ni usar el sistema para fines ilícitos."),
    ("5. Resultados y responsabilidad", "Los cálculos siguen la norma vigente y los parámetros cargados, que el cliente puede editar. El cliente debe revisar los resultados antes de pagar o declarar. La responsabilidad del proveedor se limita en los términos que se pacten por contrato."),
    ("6. Datos y confidencialidad", "El tratamiento de datos personales se rige por la Política de privacidad y por el Acuerdo de encargo de tratamiento. Planilla Ágil mantiene confidencialidad sobre la información del cliente."),
    ("7. Terminación", "El cliente puede pedir la exportación y la supresión de los datos de una empresa en cualquier momento desde el sistema. Al terminar el servicio, los datos se devuelven o suprimen según el Acuerdo de encargo."),
    ("8. Cambios", "Estos términos pueden actualizarse; se pedirá una nueva aceptación cuando cambie la versión. Ley aplicable y jurisdicción: [por definir con el abogado]."),
]
PRIVACIDAD = [
    ("1. Quiénes somos", "Planilla Ágil trata dos grupos de datos. Para los usuarios del sistema es titular del banco de datos; para los trabajadores de cada empresa cliente actúa como encargado de tratamiento y la empresa es la responsable. Contacto: " + "{contacto}."),
    ("2. Qué datos tratamos", "Usuarios: nombre, correo, rol y registros de acceso (fecha e IP). Empresa: RUC, razón social y representante. Trabajadores: nombre, DNI, cargo, fecha de ingreso, cuenta bancaria, hijos. Remuneraciones, cuota sindical y ausentismos (salud) son datos sensibles."),
    ("3. Para qué", "Dar acceso y seguridad, emitir boletas y liquidaciones, generar archivos PLAME y AFPnet, soporte y avisos del plan. No vendemos ni usamos los datos para publicidad."),
    ("4. Derechos", "Cualquier persona puede pedir información, acceso, rectificación, cancelación u oposición. Los trabajadores deben dirigirse primero a su empleador; si nos escriben a nosotros, reenviamos la solicitud a la empresa en 3 días hábiles."),
    ("5. Conservación", "Conservamos los datos mientras dure el servicio. Al terminar, los devolvemos o suprimimos; el cliente puede exportarlos y suprimirlos desde el sistema."),
    ("6. Transferencias y subencargados", "Usamos proveedores de infraestructura (alojamiento, base de datos y correo) que pueden ubicarse fuera del Perú. Se comunican al cliente y se contratan con garantías de protección equivalentes."),
    ("7. Seguridad e incidentes", "Contraseñas cifradas, acceso por roles, registro de acciones y conexión cifrada. Ante un incidente avisamos al cliente de inmediato para que cumpla su aviso a la Autoridad en 48 horas."),
    ("8. Cambios", "Publicaremos la nueva versión y pediremos aceptación cuando cambie."),
]
ENCARGO = [
    ("1. Partes", "La empresa cliente es la Responsable del tratamiento; Planilla Ágil es la Encargada. Este acuerdo se acepta al usar el sistema (aceptación registrada con versión, fecha e IP)."),
    ("2. Objeto", "La Encargada trata los datos de los trabajadores solo para calcular y emitir la planilla y sus documentos, según las instrucciones de la Responsable."),
    ("3. Confidencialidad", "La Encargada y su personal guardan reserva sobre los datos, incluso después de terminado el servicio."),
    ("4. Seguridad", "Aplica las medidas de la Política de privacidad: control de accesos por rol, contraseñas cifradas, registro de acciones y conexión cifrada, y copias de respaldo."),
    ("5. Subencargados", "La Responsable autoriza el uso de proveedores de infraestructura. La Encargada responde por ellos y informa cualquier cambio relevante."),
    ("6. Incidentes", "La Encargada avisa de inmediato a la Responsable; la Responsable notifica a la Autoridad en 48 horas cuando corresponda."),
    ("7. Derechos de los titulares", "La Encargada reenvía a la Responsable, en 3 días hábiles, las solicitudes que reciba y la apoya para atenderlas."),
    ("8. Devolución y supresión", "Al terminar el servicio, o cuando la Responsable lo pida, la Encargada devuelve los datos (exportación) y los suprime, salvo obligación legal de conservarlos."),
    ("9. Obligaciones de la Responsable", "Informar a sus trabajadores sobre el tratamiento, obtener su consentimiento cuando corresponda, inscribir su banco de datos si procede y atender sus derechos."),
]
SECCIONES = {"terminos": TERMINOS, "privacidad": PRIVACIDAD, "encargo": ENCARGO}


def html(codigo: str) -> str:
    from html import escape
    cuerpo = "".join(f"<h2>{escape(t)}</h2><p>{escape(x.replace('{contacto}', contacto()))}</p>" for t, x in SECCIONES[codigo])
    return (f"<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(TITULOS[codigo])} — {NOMBRE}</title><style>body{{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#1f2937}}"
            f"h1{{font-size:1.6rem}}h2{{font-size:1.1rem;margin-top:1.6rem}}.v{{color:#6b7280;font-size:.9rem}}a{{color:#0f766e}}</style></head><body>"
            f"<h1>{escape(TITULOS[codigo])}</h1><p class='v'>{NOMBRE} · Versión {VERSION}</p>{cuerpo}"
            f"<p class='v'><a href='/legal/terminos'>Términos</a> · <a href='/legal/privacidad'>Privacidad</a> · <a href='/legal/encargo'>Acuerdo de encargo</a></p></body></html>")
