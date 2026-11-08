-- Inmutabilidad a nivel de BASE DE DATOS (defensa adicional a la validación de la API).
-- Ejecutar en Neon DESPUÉS de `python -m app.cli init-db`.
CREATE OR REPLACE FUNCTION trg_periodo_cerrado() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_pid INT; v_estado TEXT;
BEGIN
  v_pid := CASE WHEN TG_OP = 'DELETE' THEN OLD.periodo_id ELSE NEW.periodo_id END;
  SELECT estado INTO v_estado FROM periodos WHERE id = v_pid;
  IF v_estado = 'CERRADO' THEN
    RAISE EXCEPTION 'El periodo está CERRADO. La información es inmutable.' USING ERRCODE = '42501';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE TRIGGER lock_detalle    BEFORE INSERT OR UPDATE OR DELETE ON planilla_detalle    FOR EACH ROW EXECUTE FUNCTION trg_periodo_cerrado();
CREATE TRIGGER lock_resultados BEFORE INSERT OR UPDATE OR DELETE ON planilla_resultados FOR EACH ROW EXECUTE FUNCTION trg_periodo_cerrado();
CREATE TRIGGER lock_novedades  BEFORE INSERT OR UPDATE OR DELETE ON novedades          FOR EACH ROW EXECUTE FUNCTION trg_periodo_cerrado();

-- El periodo cerrado solo se reabre desde la API (fija app.reapertura = on dentro de la transacción).
CREATE OR REPLACE FUNCTION trg_periodo_propio() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.estado = 'CERRADO' AND COALESCE(current_setting('app.reapertura', true), '') <> 'on' THEN
    RAISE EXCEPTION 'El periodo está CERRADO. Use la reapertura con motivo.' USING ERRCODE = '42501';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER lock_periodo BEFORE UPDATE OR DELETE ON periodos FOR EACH ROW EXECUTE FUNCTION trg_periodo_propio();
