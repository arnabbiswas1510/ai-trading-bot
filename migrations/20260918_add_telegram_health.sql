-- 20260918_add_telegram_health.sql
--
-- Alert-channel delivery health, persisted alongside the daily balance row.
--
-- Telegram is the ONLY alerting channel for a live-money bot, and until now a
-- delivery failure produced no durable signal anywhere: TelegramNotifier._send()
-- swallowed every error to stdout. On 2026-09-18 the channel went silent after
-- the 06:00 container restart and the outage was noticed only by its absence,
-- hours later, after five buys and a broker-side close had gone unannounced.
--
-- These two columns make the channel's state a queryable fact. The execution
-- agent refreshes them on every reconcile cycle (~15 min), so
--   telegram_consecutive_failures > 0  means alerts are being dropped RIGHT NOW
--   telegram_last_success              is how long the channel has been dead
-- and neither requires access to the container logs.
--
-- Written by execution_agent.reconcile_with_ibkr() as a SEPARATE best-effort
-- update, never as part of the balance upsert, so this migration lagging behind
-- the code can never fail the balance sync.
--
-- See decisions/2026-09-18_telegram-delivery-health.md.

ALTER TABLE account_balances
    ADD COLUMN IF NOT EXISTS telegram_consecutive_failures INTEGER,
    ADD COLUMN IF NOT EXISTS telegram_last_success         TIMESTAMPTZ;

COMMENT ON COLUMN account_balances.telegram_consecutive_failures IS
    'Consecutive Telegram delivery failures as of the last reconcile cycle. '
    '0 = channel healthy. >0 = trade alerts are being dropped. NULL = not yet reported.';

COMMENT ON COLUMN account_balances.telegram_last_success IS
    'UTC timestamp of the last confirmed Telegram delivery. Staleness against '
    'now() is how long the alert channel has been down.';
