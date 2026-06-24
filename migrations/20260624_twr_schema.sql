-- Create cash_flows table
CREATE TABLE IF NOT EXISTS cash_flows (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    date DATE NOT NULL,
    amount NUMERIC NOT NULL,
    description TEXT
);

-- Alter account_balances to add date
-- 1. Add date column
ALTER TABLE account_balances ADD COLUMN date DATE;

-- 2. Backfill existing rows with today's date
UPDATE account_balances SET date = CURRENT_DATE WHERE date IS NULL;

-- 3. Make date NOT NULL
ALTER TABLE account_balances ALTER COLUMN date SET NOT NULL;

-- 4. Drop the old primary key constraint (assuming it was on 'key')
-- Note: You might need to find the exact constraint name, usually account_balances_pkey
ALTER TABLE account_balances DROP CONSTRAINT IF EXISTS account_balances_pkey;

-- 5. Add composite primary key
ALTER TABLE account_balances ADD PRIMARY KEY (date, key);
