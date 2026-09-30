-- Apply in Supabase SQL Editor before enabling the recorder.
-- Only server-side service_role credentials may access these account snapshots.
BEGIN;

CREATE TABLE IF NOT EXISTS public.intraday_capture_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    session DATE NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    UNIQUE (run_id, sequence)
);
CREATE INDEX IF NOT EXISTS intraday_capture_events_time
    ON public.intraday_capture_events (occurred_at, id);
CREATE INDEX IF NOT EXISTS intraday_capture_events_kind_time
    ON public.intraday_capture_events (kind, occurred_at DESC);
CREATE INDEX IF NOT EXISTS intraday_capture_events_session
    ON public.intraday_capture_events (session);

CREATE TABLE IF NOT EXISTS public.intraday_capture_symbols (
    ticker TEXT PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS public.intraday_capture_health (
    id TEXT PRIMARY KEY,
    last_seen_at TIMESTAMPTZ,
    last_persisted_at TIMESTAMPTZ,
    last_error TEXT,
    error_at TIMESTAMPTZ,
    pending_events BIGINT NOT NULL DEFAULT 0,
    dropped_events BIGINT NOT NULL DEFAULT 0,
    config JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS public.intraday_replay_runs (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'rejected', 'failed')),
    automatic BOOLEAN NOT NULL DEFAULT false,
    request JSONB NOT NULL,
    summary JSONB,
    result JSONB,
    error TEXT,
    engine_revision TEXT NOT NULL DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS intraday_replay_runs_created
    ON public.intraday_replay_runs (created_at DESC);

ALTER TABLE public.intraday_capture_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_capture_symbols ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_capture_health ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_replay_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.intraday_capture_events, public.intraday_capture_symbols,
    public.intraday_capture_health, public.intraday_replay_runs FROM anon, authenticated;
GRANT ALL ON public.intraday_capture_events, public.intraday_capture_symbols,
    public.intraday_capture_health, public.intraday_replay_runs TO service_role;

CREATE OR REPLACE VIEW public.intraday_capture_sessions
WITH (security_invoker = true) AS
SELECT session,
       count(*) FILTER (WHERE kind = 'quote_sample') AS frames,
       count(*) FILTER (
           WHERE kind = 'quote_sample' AND payload->>'complete' = 'true'
       ) AS complete_frames,
       coalesce(sum(CASE
           WHEN kind = 'quote_sample'
                AND jsonb_typeof(payload->'missing_quotes') = 'array'
           THEN jsonb_array_length(payload->'missing_quotes') ELSE 0
       END), 0) AS missing_quotes,
       min(occurred_at) AS first_at,
       max(occurred_at) AS last_at
FROM public.intraday_capture_events
GROUP BY session;
REVOKE ALL ON public.intraday_capture_sessions FROM anon, authenticated;
GRANT SELECT ON public.intraday_capture_sessions TO service_role;

CREATE OR REPLACE FUNCTION public.purge_intraday_capture(keep_days INTEGER DEFAULT 365)
RETURNS BIGINT
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE removed BIGINT;
BEGIN
    IF keep_days < 1 OR keep_days > 3650 THEN
        RAISE EXCEPTION 'keep_days must be between 1 and 3650';
    END IF;
    DELETE FROM public.intraday_capture_events
      WHERE occurred_at < now() - make_interval(days => keep_days);
    GET DIAGNOSTICS removed = ROW_COUNT;
    DELETE FROM public.intraday_capture_symbols
      WHERE last_seen_at < now() - make_interval(days => keep_days);
    RETURN removed;
END;
$$;
REVOKE ALL ON FUNCTION public.purge_intraday_capture(INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.purge_intraday_capture(INTEGER) TO service_role;

COMMIT;

SELECT name, CASE WHEN to_regclass('public.' || name) IS NOT NULL
                 THEN 'OK' ELSE 'FAIL' END AS status
FROM (VALUES ('intraday_capture_events'), ('intraday_capture_symbols'),
             ('intraday_capture_health'), ('intraday_capture_sessions'),
             ('intraday_replay_runs')) AS objects(name)
UNION ALL
SELECT 'purge_intraday_capture',
       CASE WHEN to_regprocedure('public.purge_intraday_capture(integer)') IS NOT NULL
            THEN 'OK' ELSE 'FAIL' END;
