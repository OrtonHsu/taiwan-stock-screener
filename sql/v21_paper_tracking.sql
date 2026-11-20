-- V2.1 research-only paper positions. No real-money orders or account access.
-- Apply once on the connected Supabase PostgreSQL instance before enabling
-- the workflow. Objects are in a private, non-Data-API schema.
BEGIN;
CREATE SCHEMA IF NOT EXISTS stock_research;
REVOKE ALL ON SCHEMA stock_research FROM PUBLIC, anon, authenticated;
CREATE TABLE IF NOT EXISTS stock_research.simulation_positions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy_version TEXT NOT NULL CHECK (length(strategy_version) BETWEEN 3 AND 40),
    market TEXT NOT NULL CHECK (market IN ('TWSE','TPEX')),
    code TEXT NOT NULL CHECK (code ~ '^[0-9]{4}$'),
    name TEXT NOT NULL,
    setup TEXT NOT NULL,
    signal_date DATE NOT NULL,
    signal_close NUMERIC(16,4) NOT NULL CHECK (signal_close > 0),
    signal_reason TEXT NOT NULL,
    next_session_check TEXT NOT NULL,
    invalidation_reference NUMERIC(16,4) NOT NULL CHECK (invalidation_reference > 0),
    status TEXT NOT NULL DEFAULT 'pending_entry'
        CHECK (status IN ('pending_entry','open','pending_exit','closed','skipped')),
    entry_date DATE,
    entry_close NUMERIC(16,4) CHECK (entry_close > 0),
    exit_date DATE,
    exit_close NUMERIC(16,4) CHECK (exit_close > 0),
    exit_reason TEXT,
    below_ma20_streak SMALLINT NOT NULL DEFAULT 0
        CHECK (below_ma20_streak BETWEEN 0 AND 10),
    last_observed_date DATE,
    last_observed_close NUMERIC(16,4) CHECK (last_observed_close > 0),
    net_return_pct NUMERIC(12,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT sim_position_signal_once UNIQUE
        (strategy_version, market, code, signal_date),
    CONSTRAINT sim_entry_fields_together CHECK (
        (entry_date IS NULL AND entry_close IS NULL)
        OR (entry_date IS NOT NULL AND entry_close IS NOT NULL)
    ),
    CONSTRAINT sim_exit_fields_together CHECK (
        (exit_date IS NULL AND exit_close IS NULL)
        OR (exit_date IS NOT NULL AND exit_close IS NOT NULL)
    ),
    CONSTRAINT sim_closed_has_exit CHECK (
        status <> 'closed' OR
        (entry_date IS NOT NULL AND exit_date IS NOT NULL AND net_return_pct IS NOT NULL)
    )
);
-- A stock can have many historical episodes but only one active paper
-- position at a time for one fixed strategy version.
CREATE UNIQUE INDEX IF NOT EXISTS sim_one_active_per_stock
    ON stock_research.simulation_positions(strategy_version, market, code)
    WHERE status IN ('pending_entry','open','pending_exit');
CREATE INDEX IF NOT EXISTS sim_position_status_idx
    ON stock_research.simulation_positions(strategy_version,status,signal_date);
CREATE TABLE IF NOT EXISTS stock_research.simulation_daily (
    position_id BIGINT NOT NULL
        REFERENCES stock_research.simulation_positions(id) ON DELETE RESTRICT,
    asof_date DATE NOT NULL,
    close NUMERIC(16,4) CHECK (close > 0),
    ma20 NUMERIC(16,4) CHECK (ma20 > 0),
    holding_sessions INTEGER NOT NULL DEFAULT 0 CHECK (holding_sessions >= 0),
    below_ma20_streak SMALLINT NOT NULL DEFAULT 0,
    unrealized_net_pct NUMERIC(12,4),
    state TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (position_id,asof_date)
);
CREATE TABLE IF NOT EXISTS stock_research.simulation_runs (
    strategy_version TEXT NOT NULL,
    asof_date DATE NOT NULL,
    candidates INTEGER NOT NULL CHECK (candidates >= 0),
    new_signals INTEGER NOT NULL CHECK (new_signals >= 0),
    duplicates INTEGER NOT NULL CHECK (duplicates >= 0),
    active_positions INTEGER NOT NULL CHECK (active_positions >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (strategy_version,asof_date)
);
ALTER TABLE stock_research.simulation_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_research.simulation_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_research.simulation_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA stock_research FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA stock_research FROM PUBLIC, anon, authenticated;
COMMIT;
