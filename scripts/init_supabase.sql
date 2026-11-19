-- ====================================================================
-- BankOS: Script Inicial DDL y Datos Demo para Supabase (PostgreSQL)
-- Copia y pega todo este script en el SQL Editor de tu consola Supabase
-- ====================================================================

-- 1. Extensiones necesarias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 2. Tabla: TENANTS (Instituciones Financieras / Bancos)
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(50) UNIQUE NOT NULL,
    max_tx_limit NUMERIC(15, 2) NOT NULL DEFAULT 5000000.00,
    fee_type VARCHAR(20) NOT NULL DEFAULT 'percentage', -- 'fixed' o 'percentage'
    fee_value NUMERIC(10, 4) NOT NULL DEFAULT 0.0150,   -- Ej. 1.5% o valor fijo
    base_currency VARCHAR(3) NOT NULL DEFAULT 'COP',
    fx_rates JSONB NOT NULL DEFAULT '{"USD_COP": 4150.00, "EUR_COP": 4500.00, "COP_USD": 0.00024, "COP_EUR": 0.00022, "USD_USD": 1.0, "COP_COP": 1.0}'::jsonb,
    webhook_url VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Tabla: USERS (Usuarios vinculados a un Tenant específico)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email VARCHAR(150) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(150) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'cliente', -- 'administrador' o 'cliente'
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_tenant_user_email UNIQUE (tenant_id, email)
);

-- 4. Tabla: ACCOUNTS (Cuentas Bancarias con Soft Delete)
CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    account_number VARCHAR(30) NOT NULL,
    balance NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    currency VARCHAR(3) NOT NULL DEFAULT 'COP',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', -- 'ACTIVE', 'INACTIVE', 'BLOCKED'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uk_tenant_account_number UNIQUE (tenant_id, account_number),
    CONSTRAINT chk_positive_balance CHECK (balance >= 0)
);

-- 5. Tabla: TRANSACTIONS (Registro Histórico con Idempotencia)
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    idempotency_key VARCHAR(100) NOT NULL,
    correlation_id VARCHAR(100),
    operation_type VARCHAR(30) NOT NULL, -- 'DEPOSIT', 'WITHDRAW', 'TRANSFER'
    source_account_id UUID REFERENCES accounts(id) ON DELETE SET NULL,
    destination_account_id UUID REFERENCES accounts(id) ON DELETE SET NULL,
    original_amount NUMERIC(15, 2) NOT NULL,
    source_currency VARCHAR(3) NOT NULL,
    converted_amount NUMERIC(15, 2) NOT NULL,
    destination_currency VARCHAR(3) NOT NULL,
    exchange_rate NUMERIC(12, 6) NOT NULL DEFAULT 1.000000,
    fee_amount NUMERIC(15, 2) NOT NULL DEFAULT 0.00,
    status VARCHAR(20) NOT NULL DEFAULT 'SUCCESS', -- 'SUCCESS', 'FAILED'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_tenant_id ON transactions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_transactions_idempotency ON transactions(tenant_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_transactions_accounts ON transactions(source_account_id, destination_account_id);

-- 6. Tabla: AUDIT_LOGS (Registro de Auditoría Inmutable / Append-Only)
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    previous_state JSONB,
    new_state JSONB,
    ip_address VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant ON audit_logs(tenant_id, created_at DESC);

-- ====================================================================
-- DATOS SEMILLA PARA LA DEMO (2 Tenants independientes)
-- ====================================================================

-- Tenant 1: Banco Andino (Moneda Base: COP, 1.5% comisión)
INSERT INTO tenants (id, name, slug, max_tx_limit, fee_type, fee_value, base_currency, webhook_url)
VALUES (
    '11111111-1111-1111-1111-111111111111',
    'Banco Andino',
    'banco-andino',
    10000000.00,
    'percentage',
    0.0150,
    'COP',
    'https://webhook.site/demo-banco-andino'
) ON CONFLICT (slug) DO NOTHING;

-- Tenant 2: Global NeoBank (Moneda Base: USD, $2.00 comisión fija)
INSERT INTO tenants (id, name, slug, max_tx_limit, fee_type, fee_value, base_currency, webhook_url)
VALUES (
    '22222222-2222-2222-2222-222222222222',
    'Global NeoBank',
    'global-neobank',
    5000.00,
    'fixed',
    2.00,
    'USD',
    'https://webhook.site/demo-global-neobank'
) ON CONFLICT (slug) DO NOTHING;

-- Usuarios Demo (Password: '123456' hasheado con bcrypt)
-- Hash de '123456': $2b$12$e80yq9gq5o5yQJ6r5A5aYeQYpZgG4H1W1.mIeWb8eM/N2W5E5a2aK (dummy válido)
INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role)
VALUES 
(
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    '11111111-1111-1111-1111-111111111111',
    'admin@andino.com',
    '$2b$12$e80yq9gq5o5yQJ6r5A5aYeQYpZgG4H1W1.mIeWb8eM/N2W5E5a2aK',
    'Administrador Andino',
    'administrador'
),
(
    'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    '11111111-1111-1111-1111-111111111111',
    'juan@andino.com',
    '$2b$12$e80yq9gq5o5yQJ6r5A5aYeQYpZgG4H1W1.mIeWb8eM/N2W5E5a2aK',
    'Juan Perez',
    'cliente'
),
(
    'cccccccc-cccc-cccc-cccc-cccccccccccc',
    '22222222-2222-2222-2222-222222222222',
    'adrian@neobank.com',
    '$2b$12$e80yq9gq5o5yQJ6r5A5aYeQYpZgG4H1W1.mIeWb8eM/N2W5E5a2aK',
    'Adriano Cliente NeoBank',
    'cliente'
) ON CONFLICT (tenant_id, email) DO NOTHING;

-- Cuentas Bancarias Demo
INSERT INTO accounts (tenant_id, owner_id, account_number, balance, currency, status)
VALUES
(
    '11111111-1111-1111-1111-111111111111',
    'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    'CTA-COP-1001',
    5000000.00,
    'COP',
    'ACTIVE'
),
(
    '11111111-1111-1111-1111-111111111111',
    'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
    'CTA-USD-1002',
    1200.00,
    'USD',
    'ACTIVE'
),
(
    '22222222-2222-2222-2222-222222222222',
    'cccccccc-cccc-cccc-cccc-cccccccccccc',
    'CTA-NEO-2001',
    3500.00,
    'USD',
    'ACTIVE'
) ON CONFLICT (tenant_id, account_number) DO NOTHING;
