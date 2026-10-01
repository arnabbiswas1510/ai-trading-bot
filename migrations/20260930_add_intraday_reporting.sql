-- Independent cloud watchdog/report delivery. No live notification or trading tables.
BEGIN;

CREATE TABLE IF NOT EXISTS public.intraday_research_reports (
    id TEXT PRIMARY KEY,
    report_kind TEXT NOT NULL CHECK (report_kind IN ('daily', 'weekly')),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered')),
    body TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS intraday_research_reports_created
    ON public.intraday_research_reports (created_at DESC);

CREATE TABLE IF NOT EXISTS public.intraday_research_calibration_artifacts (
    id TEXT PRIMARY KEY,
    holdout_end DATE NOT NULL,
    artifact JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.intraday_research_incidents (
    id TEXT PRIMARY KEY,
    issue_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    body TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS intraday_research_incidents_open
    ON public.intraday_research_incidents (issue_key) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS public.intraday_research_delivery_receipts (
    id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL,
    recipient_hash TEXT NOT NULL,
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (notification_id, recipient_hash)
);

CREATE TABLE IF NOT EXISTS public.intraday_research_reporting_state (
    id TEXT PRIMARY KEY,
    started_on DATE NOT NULL DEFAULT (now() AT TIME ZONE 'America/New_York')::date,
    lease_owner TEXT,
    lease_until TIMESTAMPTZ
);
INSERT INTO public.intraday_research_reporting_state (id) VALUES ('scheduler')
ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.claim_intraday_reporting(worker TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    UPDATE intraday_research_reporting_state
    SET lease_owner = worker, lease_until = now() + interval '12 minutes'
    WHERE id = 'scheduler'
      AND (lease_until IS NULL OR lease_until < now());
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION public.release_intraday_reporting(worker TEXT)
RETURNS VOID
LANGUAGE sql
SET search_path = public
AS $$
    UPDATE intraday_research_reporting_state
    SET lease_owner = NULL, lease_until = NULL
    WHERE id = 'scheduler' AND lease_owner = worker;
$$;

ALTER TABLE public.intraday_research_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_research_incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_research_delivery_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_research_reporting_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_research_calibration_artifacts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.intraday_research_reports, public.intraday_research_incidents,
    public.intraday_research_delivery_receipts, public.intraday_research_reporting_state,
    public.intraday_research_calibration_artifacts
    FROM PUBLIC, anon, authenticated;
GRANT ALL ON public.intraday_research_reports, public.intraday_research_incidents,
    public.intraday_research_delivery_receipts, public.intraday_research_reporting_state,
    public.intraday_research_calibration_artifacts
    TO service_role;
REVOKE ALL ON FUNCTION public.claim_intraday_reporting(TEXT),
    public.release_intraday_reporting(TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_intraday_reporting(TEXT),
    public.release_intraday_reporting(TEXT) TO service_role;
COMMIT;

SELECT name, CASE WHEN to_regclass('public.' || name) IS NOT NULL
                 THEN 'OK' ELSE 'FAIL' END AS status
FROM (VALUES ('intraday_research_reports'), ('intraday_research_incidents'),
             ('intraday_research_delivery_receipts'), ('intraday_research_reporting_state'),
             ('intraday_research_calibration_artifacts')) AS objects(name)
UNION ALL
SELECT name, CASE WHEN to_regprocedure('public.' || name || '(text)') IS NOT NULL
                 THEN 'OK' ELSE 'FAIL' END
FROM (VALUES ('claim_intraday_reporting'), ('release_intraday_reporting')) AS routines(name);
