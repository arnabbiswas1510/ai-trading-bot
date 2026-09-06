/**
 * Commission-aware P&L accessors.
 *
 * These read fields the API already computes (backend/commissions.py) rather
 * than re-deriving the arithmetic here. That is deliberate: AGENTS.md calls out
 * frontend copies of backend constants as a known source of silent drift, and a
 * second implementation of the net-P&L rule would be exactly that. There is one
 * definition, on the server.
 *
 * The fallbacks exist only for a browser holding a response from an older
 * backend build; they degrade to the gross figure and report it as incomplete,
 * which is the same "unknown is not zero" contract the server follows.
 */

export const netPnL = (trade) =>
  trade?.net_profit_loss ?? trade?.profit_loss ?? 0;

export const grossPnL = (trade) => trade?.profit_loss ?? 0;

export const commissionOf = (trade) => trade?.total_commission ?? null;

export const isCommissionComplete = (trade) =>
  trade?.commission_complete === true;

/** Sum of net P&L, plus whether every contributing trade had fee data. */
export function netPnLTotal(trades = []) {
  const total = trades.reduce((sum, t) => sum + netPnL(t), 0);
  const complete = trades.length > 0 && trades.every(isCommissionComplete);
  return { total, complete };
}

export const PROVISIONAL_TITLE =
  'Commission not reported by IBKR for this trade — figure shown is gross ' +
  'and may overstate the true result.';
