-- Retry Treasury diagnostics only; never change frozen strategy evidence or approval.
BEGIN;

CREATE OR REPLACE FUNCTION public.intraday_calibration_refresh_risk(p_payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    proposal public.intraday_calibration_proposals%ROWTYPE;
    old_bundle jsonb;
    new_bundle jsonb;
    old_report jsonb;
    new_report jsonb;
    old_side jsonb;
    new_side jsonb;
    phase text;
    side text;
    field text;
    pending boolean;
    had_pending boolean := false;
    still_pending boolean := false;
    mutable_report text[] := ARRAY[
        'status','sha256','reference_snapshot','baseline','candidate','retry_error'];
    mutable_side text[] := ARRAY['metrics','unavailable','reference','daily_returns'];
    mutable_return text[] := ARRAY[
        'rf_return','excess_return','treasury_date','annual_yield_pct',
        'treasury_age_days','rf_unavailable'];
BEGIN
    IF jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
        OR octet_length(p_payload::text) > 32 * 1024 * 1024 THEN
        RAISE EXCEPTION 'Invalid diagnostic refresh payload' USING ERRCODE = 'P4000';
    END IF;
    IF NOT (p_payload ?& ARRAY['id','expected_revision','expected_artifact_sha256',
                              'risk_analytics','artifact_sha256','event_id'])
        OR EXISTS (SELECT 1 FROM jsonb_object_keys(p_payload) k
                   WHERE k NOT IN ('id','expected_revision','expected_artifact_sha256',
                                   'risk_analytics','artifact_sha256','event_id')) THEN
        RAISE EXCEPTION 'Unknown or missing diagnostic refresh fields' USING ERRCODE = 'P4000';
    END IF;
    FOREACH field IN ARRAY ARRAY['id','event_id'] LOOP
        IF jsonb_typeof(p_payload->field) IS DISTINCT FROM 'string'
            OR length(p_payload->>field) NOT BETWEEN 1 AND 200 THEN
            RAISE EXCEPTION 'Invalid diagnostic identifier' USING ERRCODE = 'P4000';
        END IF;
    END LOOP;
    FOREACH field IN ARRAY ARRAY['artifact_sha256','expected_artifact_sha256'] LOOP
        IF jsonb_typeof(p_payload->field) IS DISTINCT FROM 'string'
            OR p_payload->>field !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'Invalid diagnostic artifact fingerprint' USING ERRCODE = 'P4000';
        END IF;
    END LOOP;
    IF jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number'
        OR p_payload->>'expected_revision' !~ '^[0-9]{1,19}$' THEN
        RAISE EXCEPTION 'Invalid expected revision' USING ERRCODE = 'P4000';
    END IF;
    IF (p_payload->>'expected_revision')::numeric > 9223372036854775807 THEN
        RAISE EXCEPTION 'Invalid expected revision' USING ERRCODE = 'P4000';
    END IF;

    SELECT * INTO proposal FROM public.intraday_calibration_proposals
        WHERE id=p_payload->>'id' FOR UPDATE;
    IF NOT FOUND OR proposal.revision <> (p_payload->>'expected_revision')::bigint
        OR proposal.artifact_sha256 IS DISTINCT FROM p_payload->>'expected_artifact_sha256' THEN
        RAISE EXCEPTION 'Revision or artifact conflict' USING ERRCODE = 'P4090';
    END IF;
    IF proposal.status NOT IN ('evaluating','ready','no_change')
        OR (proposal.approval IS NOT NULL
            AND proposal.approval <> 'null'::jsonb AND proposal.approval <> '{}'::jsonb) THEN
        RAISE EXCEPTION 'Diagnostic refresh requires an unapproved proposal' USING ERRCODE = 'P4090';
    END IF;

    old_bundle := proposal.artifact->'risk_analytics';
    new_bundle := p_payload->'risk_analytics';
    IF jsonb_typeof(old_bundle) IS DISTINCT FROM 'object'
        OR jsonb_typeof(new_bundle) IS DISTINCT FROM 'object'
        OR old_bundle->'retry_pending' IS DISTINCT FROM 'true'::jsonb
        OR jsonb_typeof(new_bundle->'retry_pending') IS DISTINCT FROM 'boolean' THEN
        RAISE EXCEPTION 'A pending diagnostic report is required' USING ERRCODE = 'P4000';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_object_keys(old_bundle || new_bundle) k
               WHERE k NOT IN ('training','evaluation','retry_pending')) THEN
        RAISE EXCEPTION 'Unknown diagnostic phases' USING ERRCODE = 'P4000';
    END IF;
    FOREACH phase IN ARRAY ARRAY['training','evaluation'] LOOP
        IF (old_bundle ? phase) IS DISTINCT FROM (new_bundle ? phase) THEN
            RAISE EXCEPTION 'Diagnostic phases cannot be added or removed' USING ERRCODE = 'P4000';
        END IF;
        IF NOT (old_bundle ? phase) THEN
            CONTINUE;
        END IF;
        old_report := old_bundle->phase;
        new_report := new_bundle->phase;
        IF jsonb_typeof(old_report) IS DISTINCT FROM 'object'
            OR jsonb_typeof(new_report) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Diagnostic report must be an object' USING ERRCODE = 'P4000';
        END IF;
        pending := coalesce(
            jsonb_typeof(old_report->'calculation_inputs') = 'object'
            AND old_report->'calculation_inputs' <> '{}'::jsonb
            AND old_report->'reference_snapshot'->>'status' = 'unavailable'
            AND coalesce(old_report->>'retry_error','') = '', false);
        IF NOT pending THEN
            IF old_report IS DISTINCT FROM new_report THEN
                RAISE EXCEPTION 'Nonpending diagnostic report is immutable' USING ERRCODE = 'P4000';
            END IF;
            CONTINUE;
        END IF;
        had_pending := true;
        IF old_report - mutable_report IS DISTINCT FROM new_report - mutable_report THEN
            RAISE EXCEPTION 'Frozen diagnostic inputs are immutable' USING ERRCODE = 'P4000';
        END IF;
        IF old_report->'version' IS DISTINCT FROM '1'::jsonb
            OR old_report->>'phase' IS DISTINCT FROM phase
            OR jsonb_typeof(old_report->'analytics_fingerprint') IS DISTINCT FROM 'object'
            OR jsonb_typeof(old_report->'reference_window') IS DISTINCT FROM 'array'
            OR jsonb_typeof(old_report->'scope') IS DISTINCT FROM 'string'
            OR jsonb_typeof(old_report->'selected_name') IS DISTINCT FROM 'string'
            OR jsonb_typeof(old_report->'calculation_inputs'->'data') IS DISTINCT FROM 'object'
            OR jsonb_typeof(old_report->'calculation_inputs'->'baseline') IS DISTINCT FROM 'object'
            OR coalesce(jsonb_typeof(old_report->'calculation_inputs'->'candidate'),'missing')
                NOT IN ('object','null') THEN
            RAISE EXCEPTION 'Invalid frozen diagnostic shape' USING ERRCODE = 'P4000';
        END IF;
        FOREACH field IN ARRAY ARRAY['selection_sha256','benchmark_sha256','input_sha256','sha256'] LOOP
            IF jsonb_typeof(old_report->field) IS DISTINCT FROM 'string'
                OR old_report->>field !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'Invalid frozen diagnostic fingerprint' USING ERRCODE = 'P4000';
            END IF;
        END LOOP;
        IF jsonb_array_length(old_report->'reference_window') <> 2
            OR jsonb_typeof(new_report->'sha256') IS DISTINCT FROM 'string'
            OR new_report->>'sha256' !~ '^[0-9a-f]{64}$'
            OR coalesce(new_report->>'status','') NOT IN ('partial','available')
            OR jsonb_typeof(new_report->'reference_snapshot') IS DISTINCT FROM 'object'
            OR coalesce(new_report->'reference_snapshot'->>'status','')
                NOT IN ('unavailable','available') THEN
            RAISE EXCEPTION 'Invalid refreshed diagnostic shape' USING ERRCODE = 'P4000';
        END IF;
        IF new_report ? 'retry_error' AND (
            jsonb_typeof(new_report->'retry_error') IS DISTINCT FROM 'string'
            OR length(new_report->>'retry_error') NOT BETWEEN 1 AND 4000) THEN
            RAISE EXCEPTION 'Invalid diagnostic retry error' USING ERRCODE = 'P4000';
        END IF;

        FOREACH side IN ARRAY ARRAY['baseline','candidate'] LOOP
            old_side := old_report->side;
            new_side := new_report->side;
            IF jsonb_typeof(old_side) IS DISTINCT FROM 'object'
                OR jsonb_typeof(new_side) IS DISTINCT FROM 'object' THEN
                RAISE EXCEPTION 'Risk results must be objects' USING ERRCODE = 'P4000';
            END IF;
            -- An unmodelled candidate has no metrics to recompute.
            IF old_report->'calculation_inputs'->side = 'null'::jsonb THEN
                IF old_side IS DISTINCT FROM new_side THEN
                    RAISE EXCEPTION 'Unmodelled risk results are immutable' USING ERRCODE = 'P4000';
                END IF;
                CONTINUE;
            END IF;
            IF old_side - mutable_side IS DISTINCT FROM new_side - mutable_side
                OR old_side->'daily_equity' IS DISTINCT FROM new_side->'daily_equity' THEN
                RAISE EXCEPTION 'Non-Treasury risk results are immutable' USING ERRCODE = 'P4000';
            END IF;
            FOREACH field IN ARRAY ARRAY['metrics','unavailable'] LOOP
                IF jsonb_typeof(old_side->field) IS DISTINCT FROM 'object'
                    OR jsonb_typeof(new_side->field) IS DISTINCT FROM 'object' THEN
                    RAISE EXCEPTION 'Invalid risk metric shape' USING ERRCODE = 'P4000';
                END IF;
                IF (old_side->field) - ARRAY['sharpe','sortino'] IS DISTINCT FROM
                    (new_side->field) - ARRAY['sharpe','sortino'] THEN
                    RAISE EXCEPTION 'Non-Treasury metrics are immutable' USING ERRCODE = 'P4000';
                END IF;
            END LOOP;
            IF jsonb_typeof(new_side->'reference') IS DISTINCT FROM 'object'
                OR jsonb_typeof(old_side->'daily_equity') IS DISTINCT FROM 'array'
                OR jsonb_typeof(old_side->'daily_returns') IS DISTINCT FROM 'array'
                OR jsonb_typeof(new_side->'daily_returns') IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'Invalid risk series shape' USING ERRCODE = 'P4000';
            END IF;
            IF jsonb_array_length(old_side->'daily_returns') <>
                jsonb_array_length(new_side->'daily_returns')
                OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements(old_side->'daily_returns')
                        WITH ORDINALITY AS old_row(value, position)
                    JOIN jsonb_array_elements(new_side->'daily_returns')
                        WITH ORDINALITY AS new_row(value, position) USING (position)
                    WHERE jsonb_typeof(old_row.value) <> 'object'
                        OR jsonb_typeof(new_row.value) <> 'object'
                        OR old_row.value - mutable_return IS DISTINCT FROM new_row.value - mutable_return
                ) THEN
                RAISE EXCEPTION 'Frozen daily returns are immutable' USING ERRCODE = 'P4000';
            END IF;
        END LOOP;
        still_pending := still_pending OR (
            new_report->'reference_snapshot'->>'status' = 'unavailable'
            AND NOT (new_report ? 'retry_error'));
    END LOOP;
    IF NOT had_pending OR new_bundle->'retry_pending' IS DISTINCT FROM to_jsonb(still_pending) THEN
        RAISE EXCEPTION 'Invalid diagnostic retry state' USING ERRCODE = 'P4000';
    END IF;

    -- The trusted Python caller hashes this exact replacement using artifact_digest;
    -- PostgreSQL JSONB text has different whitespace and is not that canonical encoding.
    UPDATE public.intraday_calibration_proposals SET
        artifact=jsonb_set(artifact, '{risk_analytics}', new_bundle, false),
        artifact_sha256=p_payload->>'artifact_sha256',
        revision=revision+1, updated_at=clock_timestamp()
        WHERE id=proposal.id RETURNING * INTO proposal;
    INSERT INTO public.intraday_calibration_events(id,proposal_id,event,note,data)
    VALUES (p_payload->>'event_id',proposal.id,'risk_reference_retry',
        'Retried missing Treasury diagnostics over saved outputs; strategy and approval rules unchanged.',
        jsonb_build_object('revision',proposal.revision,
            'expected_artifact_sha256',p_payload->>'expected_artifact_sha256',
            'artifact_sha256',proposal.artifact_sha256,'retry_pending',still_pending));
    RETURN to_jsonb(proposal) - 'initial_content';
END;
$$;

REVOKE ALL ON FUNCTION public.intraday_calibration_refresh_risk(jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.intraday_calibration_refresh_risk(jsonb) TO service_role;

COMMIT;

SELECT 'intraday_calibration_refresh_risk function' AS object,
       CASE WHEN EXISTS (
           SELECT 1 FROM pg_proc
           WHERE oid=to_regprocedure('public.intraday_calibration_refresh_risk(jsonb)')
               AND prosecdef AND proconfig @> ARRAY['search_path=public']
       ) THEN 'OK' ELSE 'FAIL' END AS status
UNION ALL
SELECT 'intraday_calibration_refresh_risk service-only grants',
       CASE WHEN coalesce(has_function_privilege('service_role',
                     to_regprocedure('public.intraday_calibration_refresh_risk(jsonb)'), 'EXECUTE'), false)
           AND NOT coalesce(has_function_privilege('anon',
                     to_regprocedure('public.intraday_calibration_refresh_risk(jsonb)'), 'EXECUTE'), true)
           AND NOT coalesce(has_function_privilege('authenticated',
                     to_regprocedure('public.intraday_calibration_refresh_risk(jsonb)'), 'EXECUTE'), true)
           AND NOT EXISTS (
               SELECT 1 FROM pg_proc p,
                   LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
               WHERE p.oid=to_regprocedure('public.intraday_calibration_refresh_risk(jsonb)')
                   AND a.grantee=0 AND a.privilege_type='EXECUTE'
           ) THEN 'OK' ELSE 'FAIL' END;
