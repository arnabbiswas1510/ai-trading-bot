-- Private research only: no live configuration, trading permissions or orders.
BEGIN;

CREATE TABLE IF NOT EXISTS public.intraday_calibration_settings (
    id text PRIMARY KEY CHECK (id = 'default'),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    value jsonb NOT NULL
);
INSERT INTO public.intraday_calibration_settings (id, value)
VALUES ('default', '{"enabled":true,"training_sessions":5,"evaluation_sessions":5,"max_candidates":16,"risk_policy":null}')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.intraday_calibration_proposals (
    id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('parameter','rule')),
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    parent_id text REFERENCES public.intraday_calibration_proposals(id),
    status text NOT NULL CHECK (status IN ('evaluating','ready','no_change','blocked',
        'investigation_requested','investigation_approved','needs_engine_support',
        'rejected','deferred','approved')),
    revision bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    artifact jsonb NOT NULL DEFAULT '{}',
    artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    request jsonb NOT NULL DEFAULT '{}',
    approval jsonb,
    initial_content jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS public.intraday_calibration_events (
    id text PRIMARY KEY,
    proposal_id text REFERENCES public.intraday_calibration_proposals(id),
    event text NOT NULL,
    note text NOT NULL DEFAULT '',
    data jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    notified_at timestamptz
);
CREATE INDEX IF NOT EXISTS intraday_calibration_pending_events
ON public.intraday_calibration_events (created_at) WHERE notified_at IS NULL;
CREATE INDEX IF NOT EXISTS intraday_calibration_proposal_events
ON public.intraday_calibration_events (proposal_id, created_at);
CREATE TABLE IF NOT EXISTS public.intraday_calibration_health (
    id text PRIMARY KEY CHECK (id = 'calibration-worker'),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL,
    last_error text,
    metrics jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS public.intraday_calibration_lease (
    id text PRIMARY KEY CHECK (id = 'calibration-worker'),
    owner text,
    until_at timestamptz
);
INSERT INTO public.intraday_calibration_lease(id) VALUES ('calibration-worker')
ON CONFLICT (id) DO NOTHING;

-- Service-role callers are trusted application code. Browser roles have neither
-- table access nor RPC access. The invoker function cannot elevate a caller.
ALTER TABLE public.intraday_calibration_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_calibration_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_calibration_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_calibration_health ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intraday_calibration_lease ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.intraday_calibration_settings, public.intraday_calibration_proposals,
    public.intraday_calibration_events, public.intraday_calibration_health,
    public.intraday_calibration_lease FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.intraday_calibration_settings,
    public.intraday_calibration_proposals, public.intraday_calibration_health,
    public.intraday_calibration_lease TO service_role;
GRANT SELECT, INSERT ON public.intraday_calibration_events TO service_role;
GRANT UPDATE (notified_at) ON public.intraday_calibration_events TO service_role;

CREATE OR REPLACE FUNCTION public.intraday_calibration_mutate(p_operation text, p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE
    proposal public.intraday_calibration_proposals%ROWTYPE;
    investigation public.intraday_calibration_proposals%ROWTYPE;
    settings public.intraday_calibration_settings%ROWTYPE;
    audit public.intraday_calibration_events%ROWTYPE;
    row_data jsonb;
    patch jsonb;
    target text;
    source_status text;
    previous text;
    result jsonb;
    changed integer;
    experiment jsonb;
BEGIN
    IF p_operation = 'create' THEN
        row_data := p_payload->'row';
        IF row_data->>'status' NOT IN ('evaluating','ready','no_change','blocked',
            'investigation_requested','investigation_approved','needs_engine_support')
            OR row_data ? 'approval' THEN
            RAISE EXCEPTION 'Invalid initial state' USING ERRCODE = 'P4000';
        END IF;
        INSERT INTO public.intraday_calibration_proposals
            (id,kind,title,parent_id,status,artifact,artifact_sha256,request,initial_content)
        VALUES (row_data->>'id',row_data->>'kind',row_data->>'title',row_data->>'parent_id',
            row_data->>'status',row_data->'artifact',row_data->>'artifact_sha256',
            row_data->'request',row_data)
        ON CONFLICT (id) DO NOTHING;
        GET DIAGNOSTICS changed = ROW_COUNT;
        SELECT * INTO proposal FROM public.intraday_calibration_proposals
            WHERE id = row_data->>'id' FOR UPDATE;
        IF proposal.initial_content IS DISTINCT FROM row_data THEN
            RAISE EXCEPTION 'Idempotency conflict' USING ERRCODE = 'P4090';
        END IF;
        IF changed > 0 THEN
            INSERT INTO public.intraday_calibration_events(id,proposal_id,event,data)
            VALUES ('created:' || proposal.id,proposal.id,'created',
                jsonb_build_object('status',proposal.status));
        END IF;
        RETURN to_jsonb(proposal) - 'initial_content';
    ELSIF p_operation = 'settings' THEN
        SELECT * INTO settings FROM public.intraday_calibration_settings WHERE id='default' FOR UPDATE;
        IF NOT FOUND OR settings.revision <> (p_payload->>'expected_revision')::bigint THEN
            RAISE EXCEPTION 'Revision conflict' USING ERRCODE = 'P4090';
        END IF;
        UPDATE public.intraday_calibration_settings
            SET value=p_payload->'value',revision=revision+1 WHERE id='default'
            RETURNING * INTO settings;
        INSERT INTO public.intraday_calibration_events(id,event,data)
        VALUES ('settings:' || settings.revision,'settings_updated',
            jsonb_build_object('actor','operator','revision',settings.revision,'value',settings.value));
        RETURN jsonb_build_object('revision',settings.revision,'value',settings.value);
    ELSIF p_operation = 'update' THEN
        -- Lock policy first, in the same order as approval/export transactions.
        SELECT * INTO settings FROM public.intraday_calibration_settings WHERE id='default' FOR SHARE;
        SELECT * INTO proposal FROM public.intraday_calibration_proposals
            WHERE id=p_payload->>'id' FOR UPDATE;
        IF NOT FOUND OR proposal.revision <> (p_payload->>'expected_revision')::bigint THEN
            RAISE EXCEPTION 'Revision conflict' USING ERRCODE = 'P4090';
        END IF;
        patch := p_payload->'patch';
        source_status := proposal.status;
        target := coalesce(patch->>'status',proposal.status);
        experiment := proposal.artifact->'frozen'->'selection'->'frozen_experiment';
        IF p_payload->>'event' IN ('approve_deployment','deployment_export')
            AND experiment->'exit_config' ? 'scale_out_enabled'
            AND experiment->'exit_config'->'scale_out_enabled' IS DISTINCT FROM
                proposal.artifact->'frozen'->'selection'->'original_settings'->'exit_config'->'scale_out_enabled' THEN
            SELECT * INTO investigation FROM public.intraday_calibration_proposals
                WHERE id=proposal.parent_id FOR SHARE;
            IF NOT FOUND OR investigation.kind <> 'rule'
                OR investigation.status NOT IN ('investigation_approved','no_change')
                OR investigation.request->>'campaign_id' IS DISTINCT FROM proposal.id
                OR investigation.request->'experiment' IS DISTINCT FROM experiment
                OR NOT EXISTS (
                    SELECT 1 FROM public.intraday_calibration_events
                    WHERE proposal_id=investigation.id AND event='approve_investigation'
                        AND data->>'actor'='operator'
                        AND created_at < (proposal.artifact->>'frozen_at')::timestamptz
                ) THEN
                RAISE EXCEPTION 'Prior exact investigation approval required' USING ERRCODE = 'P4090';
            END IF;
        END IF;
        IF p_payload->>'event' = 'deployment_export' AND (
            proposal.status <> 'approved'
            OR proposal.artifact->'frozen'->'settings'->'risk_policy' IS DISTINCT FROM settings.value->'risk_policy'
            OR settings.value->'risk_policy' IS NULL OR settings.value->'risk_policy' = 'null'::jsonb
            OR (proposal.artifact->>'settings_revision')::bigint IS DISTINCT FROM settings.revision
            OR proposal.artifact->'evaluation_complete' IS DISTINCT FROM 'true'::jsonb
            OR proposal.artifact->>'evaluation_end' IS NULL
            OR proposal.artifact->>'last_evaluated_session' IS DISTINCT FROM proposal.artifact->>'evaluation_end'
            OR proposal.approval->>'artifact_sha256' IS DISTINCT FROM proposal.artifact_sha256
            OR (proposal.approval->>'settings_revision')::bigint IS DISTINCT FROM settings.revision
            OR (p_payload->'data'->>'expected_policy_revision')::bigint IS DISTINCT FROM settings.revision
            OR p_payload->'data'->>'artifact_sha256' IS DISTINCT FROM proposal.artifact_sha256
        ) THEN
            RAISE EXCEPTION 'Approval revoked or policy changed' USING ERRCODE = 'P4090';
        END IF;
        IF EXISTS (SELECT 1 FROM jsonb_object_keys(patch) k
                   WHERE k NOT IN ('status','artifact','artifact_sha256','request','approval')) THEN
            RAISE EXCEPTION 'Unknown update' USING ERRCODE = 'P4000';
        END IF;
        IF (patch ? 'artifact' OR patch ? 'artifact_sha256') AND
           proposal.status NOT IN ('evaluating','investigation_approved') THEN
            RAISE EXCEPTION 'Frozen evidence is immutable' USING ERRCODE = 'P4000';
        END IF;
        IF proposal.status = 'approved' THEN
            IF NOT (patch = '{}'::jsonb OR
                (target='rejected' AND p_payload->>'event'='revoke'
                 AND patch = '{"status":"rejected"}'::jsonb)) THEN
                RAISE EXCEPTION 'Revoke exact approval before changing state' USING ERRCODE = 'P4000';
            END IF;
        ELSIF proposal.status = 'rejected' AND patch <> '{}'::jsonb THEN
            RAISE EXCEPTION 'Rejected proposal is terminal' USING ERRCODE = 'P4000';
        END IF;
        IF target <> proposal.status THEN
            IF target = 'approved' THEN
                IF proposal.status <> 'ready' OR p_payload->>'event' <> 'approve_deployment'
                    OR patch ? 'artifact' OR patch ? 'request'
                    OR settings.value->'risk_policy' IS NULL
                    OR settings.value->'risk_policy' = 'null'::jsonb
                    OR proposal.artifact->'frozen'->'settings'->'risk_policy' IS DISTINCT FROM settings.value->'risk_policy'
                    OR (proposal.artifact->>'settings_revision')::bigint IS DISTINCT FROM settings.revision
                    OR proposal.artifact->'evaluation_complete' IS DISTINCT FROM 'true'::jsonb
                    OR proposal.artifact->>'evaluation_end' IS NULL
                    OR proposal.artifact->>'last_evaluated_session' IS DISTINCT FROM proposal.artifact->>'evaluation_end'
                    OR patch->'approval'->>'artifact_sha256' IS DISTINCT FROM proposal.artifact_sha256
                    OR (patch->'approval'->>'settings_revision')::bigint IS DISTINCT FROM settings.revision
                    OR (patch->'approval'->>'policy_revision')::bigint IS DISTINCT FROM settings.revision
                    OR (patch->'approval'->>'proposal_revision')::bigint IS DISTINCT FROM proposal.revision + 1
                    OR patch->'approval'->>'actor' IS DISTINCT FROM 'operator' THEN
                    RAISE EXCEPTION 'Approval evidence or policy changed' USING ERRCODE = 'P4090';
                END IF;
            ELSIF target = 'rejected' THEN
                IF p_payload->>'event' NOT IN ('reject','revoke') THEN
                    RAISE EXCEPTION 'Invalid rejection' USING ERRCODE = 'P4000';
                END IF;
            ELSIF target = 'deferred' THEN
                IF p_payload->>'event' <> 'defer' THEN
                    RAISE EXCEPTION 'Invalid deferral' USING ERRCODE = 'P4000';
                END IF;
            ELSIF proposal.status = 'deferred' THEN
                SELECT data->>'previous_status' INTO previous FROM public.intraday_calibration_events
                    WHERE proposal_id=proposal.id AND event='defer' ORDER BY created_at DESC LIMIT 1;
                IF p_payload->>'event' <> 'resume' OR target IS DISTINCT FROM previous THEN
                    RAISE EXCEPTION 'Invalid resume' USING ERRCODE = 'P4000';
                END IF;
            ELSIF proposal.status = 'investigation_requested' THEN
                IF proposal.kind <> 'rule' OR p_payload->>'event' <> 'approve_investigation'
                    OR target NOT IN ('investigation_approved','needs_engine_support') THEN
                    RAISE EXCEPTION 'Investigation approval required' USING ERRCODE = 'P4000';
                END IF;
            ELSIF proposal.status = 'investigation_approved' THEN
                IF target NOT IN ('evaluating','ready','no_change','blocked','needs_engine_support') THEN
                    RAISE EXCEPTION 'Invalid investigation result' USING ERRCODE = 'P4000';
                END IF;
            ELSIF proposal.status = 'evaluating' THEN
                IF target NOT IN ('ready','no_change','blocked') THEN
                    RAISE EXCEPTION 'Invalid evaluation result' USING ERRCODE = 'P4000';
                END IF;
            ELSE
                RAISE EXCEPTION 'Invalid transition' USING ERRCODE = 'P4000';
            END IF;
        END IF;
        IF patch ? 'approval' AND target <> 'approved' THEN
            RAISE EXCEPTION 'Approval only valid on approval transition' USING ERRCODE = 'P4000';
        END IF;
        UPDATE public.intraday_calibration_proposals SET
            status=target, revision=revision+1, updated_at=clock_timestamp(),
            artifact=coalesce(patch->'artifact',artifact),
            artifact_sha256=coalesce(patch->>'artifact_sha256',artifact_sha256),
            request=coalesce(patch->'request',request),
            approval=CASE WHEN patch ? 'approval' THEN patch->'approval' ELSE approval END
            WHERE id=proposal.id RETURNING * INTO proposal;
        IF p_payload ? 'child' THEN
            IF p_payload->>'event' NOT IN ('request_experiment','experiment_enrolled')
                OR p_payload->'child'->>'parent_id' IS DISTINCT FROM proposal.id THEN
                RAISE EXCEPTION 'Invalid linked experiment' USING ERRCODE = 'P4000';
            END IF;
            IF p_payload->>'event' = 'experiment_enrolled' AND (
                source_status <> 'investigation_approved' OR target <> 'no_change'
                OR p_payload->'child'->>'status' NOT IN ('evaluating','no_change')
                OR p_payload->'child'->'request' IS DISTINCT FROM '{}'::jsonb
                OR proposal.request->>'campaign_id' IS DISTINCT FROM p_payload->'child'->>'id'
            ) THEN
                RAISE EXCEPTION 'Invalid atomic investigation enrollment' USING ERRCODE = 'P4000';
            END IF;
            result := public.intraday_calibration_mutate('create',
                jsonb_build_object('row',p_payload->'child'));
        END IF;
        INSERT INTO public.intraday_calibration_events(id,proposal_id,event,note,data)
        VALUES (p_payload->>'event_id',proposal.id,p_payload->>'event',
            coalesce(p_payload->>'note',''),coalesce(p_payload->'data','{}'::jsonb));
        RETURN to_jsonb(proposal) - 'initial_content';
    ELSIF p_operation = 'event' THEN
        INSERT INTO public.intraday_calibration_events(id,proposal_id,event,note,data)
        VALUES (p_payload->>'id',p_payload->>'proposal_id',p_payload->>'event',
            p_payload->>'note',p_payload->'data') ON CONFLICT (id) DO NOTHING;
        SELECT * INTO audit FROM public.intraday_calibration_events WHERE id=p_payload->>'id';
        IF audit.proposal_id IS DISTINCT FROM p_payload->>'proposal_id'
            OR audit.event IS DISTINCT FROM p_payload->>'event'
            OR audit.note IS DISTINCT FROM p_payload->>'note'
            OR audit.data IS DISTINCT FROM p_payload->'data' THEN
            RAISE EXCEPTION 'Event idempotency conflict' USING ERRCODE = 'P4090';
        END IF;
        RETURN to_jsonb(audit);
    ELSIF p_operation = 'notified' THEN
        UPDATE public.intraday_calibration_events SET notified_at=coalesce(notified_at,clock_timestamp())
            WHERE id=p_payload->>'id';
        GET DIAGNOSTICS changed = ROW_COUNT;
        RETURN to_jsonb(changed > 0);
    ELSIF p_operation = 'health' THEN
        INSERT INTO public.intraday_calibration_health(id,last_seen_at,status,last_error,metrics)
        VALUES ('calibration-worker',clock_timestamp(),p_payload->>'status',
                p_payload->>'last_error',p_payload->'metrics')
        ON CONFLICT (id) DO UPDATE SET last_seen_at=excluded.last_seen_at,
            status=excluded.status,last_error=excluded.last_error,metrics=excluded.metrics
        RETURNING to_jsonb(intraday_calibration_health.*) INTO result;
        RETURN result;
    ELSIF p_operation = 'claim' THEN
        IF (p_payload->>'seconds')::int NOT BETWEEN 1 AND 3600 THEN
            RAISE EXCEPTION 'Invalid lease duration' USING ERRCODE = 'P4000';
        END IF;
        UPDATE public.intraday_calibration_lease
            SET owner=p_payload->>'owner',
                until_at=clock_timestamp()+make_interval(secs => (p_payload->>'seconds')::int)
            WHERE id='calibration-worker' AND
                (until_at IS NULL OR until_at <= clock_timestamp() OR owner=p_payload->>'owner');
        GET DIAGNOSTICS changed = ROW_COUNT;
        RETURN to_jsonb(changed = 1);
    ELSIF p_operation = 'release' THEN
        UPDATE public.intraday_calibration_lease SET owner=NULL,until_at=NULL
            WHERE id='calibration-worker' AND owner=p_payload->>'owner';
        GET DIAGNOSTICS changed = ROW_COUNT;
        RETURN to_jsonb(changed = 1);
    END IF;
    RAISE EXCEPTION 'Unknown calibration operation' USING ERRCODE = 'P4000';
END $$;
REVOKE ALL ON FUNCTION public.intraday_calibration_mutate(text,jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.intraday_calibration_mutate(text,jsonb) TO service_role;

SELECT name, CASE WHEN to_regclass('public.' || name) IS NOT NULL THEN 'OK' ELSE 'FAIL' END AS status
FROM unnest(ARRAY['intraday_calibration_settings','intraday_calibration_proposals',
    'intraday_calibration_events','intraday_calibration_health','intraday_calibration_lease']) AS name
UNION ALL
SELECT 'intraday_calibration_mutate',
    CASE WHEN to_regprocedure('public.intraday_calibration_mutate(text,jsonb)') IS NOT NULL
    THEN 'OK' ELSE 'FAIL' END;
COMMIT;
