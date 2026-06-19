# Replace the blocking trigger with a LOGGING-ONLY version
# This logs the caller identity WITHOUT blocking the delete
# So legitimate stop-loss sells still work
# But we still capture WHO is doing the delete via Supabase logs

LOGGING_TRIGGER_SQL = """
-- Step 1: Create a log table (if it doesn't exist)
CREATE TABLE IF NOT EXISTS public.portfolio_delete_log (
    id            BIGSERIAL PRIMARY KEY,
    ticker        TEXT,
    deleted_at    TIMESTAMPTZ DEFAULT NOW(),
    pg_user       TEXT,
    app_name      TEXT,
    client_addr   TEXT,
    pid           INT,
    query_text    TEXT
);

-- Step 2: Replace blocking trigger with logging-only version
CREATE OR REPLACE FUNCTION public.log_portfolio_deletion()
RETURNS TRIGGER AS $$
DECLARE
    v_app_name    TEXT;
    v_client_addr TEXT;
    v_pid         INT;
    v_query       TEXT;
BEGIN
    SELECT application_name, client_addr::TEXT, pid, LEFT(query, 500)
    INTO v_app_name, v_client_addr, v_pid, v_query
    FROM pg_stat_activity WHERE pid = pg_backend_pid();

    INSERT INTO public.portfolio_delete_log
        (ticker, pg_user, app_name, client_addr, pid, query_text)
    VALUES (
        OLD.ticker,
        current_user,
        COALESCE(v_app_name, 'unknown'),
        COALESCE(v_client_addr, 'unknown'),
        COALESCE(v_pid, 0),
        COALESCE(v_query, 'n/a')
    );

    -- ALLOW the delete (return OLD, don't raise exception)
    RETURN OLD;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Step 3: Replace the blocking trigger with the logging trigger
DROP TRIGGER IF EXISTS block_portfolio_deletion ON public.portfolio_positions;
CREATE TRIGGER log_portfolio_deletion
    BEFORE DELETE ON public.portfolio_positions
    FOR EACH ROW EXECUTE FUNCTION public.log_portfolio_deletion();
"""

print("Copy the SQL below into your Supabase SQL editor and run it:")
print("=" * 60)
print(LOGGING_TRIGGER_SQL)
print("=" * 60)
print("""
After running this SQL:
- Deletes are NO LONGER BLOCKED (so legitimate stop-loss sells work)
- Every delete is LOGGED to portfolio_delete_log table
- To see who deleted MS: 
    SELECT * FROM portfolio_delete_log ORDER BY deleted_at DESC LIMIT 20;
""")
