"""Configuración guiada: crea las tablas en Neon, activa el bloqueo de periodos cerrados y genera su clave."""
import os
import sys

print("\n=== PLANILLA ÁGIL · CONFIGURACIÓN INICIAL ===\n")
print("1) Entre a console.neon.tech, abra su proyecto y pulse el botón 'Connect'.")
print("2) Copie la cadena que empieza con postgresql://  (con la contraseña visible).\n")
url = input("Pegue aquí la cadena y pulse Enter:\n> ").strip().strip('"')
if not (url.startswith("postgres") or url.startswith("sqlite")):
    print("\nEso no parece una cadena de Neon (debe empezar con postgresql://). Vuelva a abrir este archivo e intente de nuevo.")
    sys.exit(1)
os.environ["DATABASE_URL"] = url

try:
    from sqlalchemy import text
    from app.cli import crear_cuenta, init_db
    from app.db import engine
except Exception as e:  # noqa
    print("\nNo se pudo cargar el programa:", e)
    sys.exit(1)

try:
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
except Exception as e:  # noqa
    print("\nNo se pudo conectar a Neon. Revise que la cadena esté completa y con la contraseña.\nDetalle:", str(e).splitlines()[0])
    sys.exit(1)
print("\n✓ Conexión con Neon correcta.")

if engine.dialect.name == "postgresql":
    r = input("\n¿Borrar TODO lo que haya en esta base y empezar limpio? (recomendado en la primera vez) [S/N]: ").strip().upper()
    if r == "S":
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        print("✓ Base limpia.")

init_db()

if engine.dialect.name == "postgresql":
    ruta = os.path.join(os.path.dirname(__file__), "sql", "integridad_postgres.sql")
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(open(ruta, encoding="utf-8").read())
        raw.commit()
        print("✓ Bloqueo de periodos cerrados activado.")
    except Exception as e:  # noqa
        raw.rollback()
        print("Aviso: el bloqueo ya estaba activado o no se pudo crear:", str(e).splitlines()[0])
    finally:
        raw.close()

clave = crear_cuenta("Mi cuenta", "GRATIS")
with open("MI_CLAVE.txt", "w", encoding="utf-8") as f:
    f.write("TU CLAVE DE API (cópiala en el HTML, engranaje > Clave):\n\n" + clave + "\n")
print("\n✓ LISTO. Su clave quedó guardada en el archivo MI_CLAVE.txt (se abrirá ahora).")
try:
    os.startfile("MI_CLAVE.txt")  # solo Windows
except Exception:  # noqa
    pass
