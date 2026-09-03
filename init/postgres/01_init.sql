-- EBM Analyzer – PostgreSQL Initialisierungsskript
-- Wird einmalig beim ersten Container-Start ausgeführt.
-- SQLAlchemy create_all() übernimmt die eigentliche Schema-Erstellung.
-- Hier: Erweiterungen + Performance-Einstellungen.

-- UUID-Erweiterung (für zukünftige gen_random_uuid() Verwendung)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Lokale Zeitzone setzen
ALTER DATABASE ebm_db SET timezone TO 'Europe/Berlin';

-- Optimierte Einstellungen für medizinische Workloads
ALTER SYSTEM SET log_min_duration_statement = '1000';  -- Queries > 1s loggen

-- Berechtigungen
GRANT ALL PRIVILEGES ON DATABASE ebm_db TO ebm_user;

-- Kommentar zur Datenbankdokumentation
COMMENT ON DATABASE ebm_db IS
  'EBM Analyzer – DSGVO-konformes, mandantenfähiges Dokumentationssystem für § 203 StGB-konforme medizinische Abrechnung';
