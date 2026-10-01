-- Private hypothetical decisions only. Never brokerage instructions.
CREATE TABLE IF NOT EXISTS public.intraday_shadow_runs (
    id text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL,
    initial_state jsonb NOT NULL,
    effective_config jsonb NOT NULL,
    engine_revision text NOT NULL,
    latest_sequence bigint NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS public.intraday_shadow_events (
    id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES public.intraday_shadow_runs(id) ON DELETE CASCADE,
    sequence bigint NOT NULL,
    session date NOT NULL,
    occurred_at timestamptz NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    input_sha256 text,
    state_sha256 text,
    UNIQUE (run_id, sequence)
);
CREATE TABLE IF NOT EXISTS public.intraday_shadow_checkpoints (
    run_id text NOT NULL REFERENCES public.intraday_shadow_runs(id) ON DELETE CASCADE,
    sequence bigint NOT NULL,
    state jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, sequence)
);
CREATE TABLE IF NOT EXISTS public.intraday_shadow_health (
    id text PRIMARY KEY CHECK (id = 'shadow-worker'),
    last_seen_at timestamptz,
    last_cycle_at timestamptz,
    last_persisted_at timestamptz,
    status text NOT NULL,
    last_error text,
    run_id text REFERENCES public.intraday_shadow_runs(id),
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS intraday_shadow_events_window
    ON public.intraday_shadow_events(session, run_id, sequence);
ALTER TABLE public.intraday_shadow_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_shadow_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_shadow_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_shadow_health ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.intraday_shadow_runs, public.intraday_shadow_events,
    public.intraday_shadow_checkpoints, public.intraday_shadow_health FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.intraday_shadow_runs,
    public.intraday_shadow_events, public.intraday_shadow_checkpoints,
    public.intraday_shadow_health TO service_role;
-- Retention must remove whole inactive runs, not prefixes: replay/calibration
-- needs seed + every subsequent frame. Running/blocked runs are never truncated.
-- An operator may archive and delete superseded runs only after saving their
-- complete dataset and dependent research reports; no automatic purge is installed.
SELECT name,
       CASE WHEN to_regclass('public.' || name) IS NOT NULL THEN 'OK' ELSE 'FAIL' END AS status
FROM (VALUES ('intraday_shadow_runs'), ('intraday_shadow_events'),
             ('intraday_shadow_checkpoints'), ('intraday_shadow_health')) AS required(name);
