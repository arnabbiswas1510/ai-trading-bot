import React from 'react';
import {
  Bot, Building2, User, HelpCircle, AlertTriangle, Calendar, Hash, Quote,
} from 'lucide-react';
import {
  classifyExit, deriveTradeMath, extractReasonFacts, unrecordedFields,
} from '../lib/exitDetails';

const money = (v) =>
  v === null || v === undefined || !Number.isFinite(v)
    ? '—'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(v);

const pct = (v) =>
  v === null || v === undefined || !Number.isFinite(v)
    ? '—'
    : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;

const EXECUTOR_ICON = {
  BOT: Bot,
  BROKER: Building2,
  MANUAL: User,
  UNKNOWN: HelpCircle,
};

const EXECUTOR_COLOR = {
  BOT: '#8b5cf6',
  BROKER: '#3b82f6',
  MANUAL: '#f59e0b',
  UNKNOWN: 'var(--text-muted)',
};

function Field({ label, value, color, mono = true }) {
  return (
    <div>
      <div style={{
        fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: '0.06em',
        color: 'var(--text-muted)', fontWeight: 600, marginBottom: '0.2rem',
      }}>
        {label}
      </div>
      <div style={{
        fontSize: '0.88rem', fontWeight: 600,
        fontFamily: mono ? 'var(--font-display)' : 'inherit',
        color: color || 'var(--text-primary)',
      }}>
        {value}
      </div>
    </div>
  );
}

function Section({ title, children, style }) {
  return (
    <div style={{
      background: 'rgba(255,255,255,0.02)',
      border: '1px solid var(--border-light)',
      borderRadius: '8px',
      padding: '0.85rem 1rem',
      ...style,
    }}>
      <div style={{
        fontSize: '0.66rem', textTransform: 'uppercase', letterSpacing: '0.08em',
        color: 'var(--text-muted)', fontWeight: 700, marginBottom: '0.7rem',
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function formatFact(fact) {
  switch (fact.kind) {
    case 'money': return money(fact.value);
    case 'percent': return pct(fact.value);
    case 'int': return String(fact.value);
    case 'number': return Number(fact.value).toFixed(1);
    default: return String(fact.value);
  }
}

/**
 * Full breakdown of how a position was closed.
 *
 * Every number shown here is either read directly out of `trade_history` or
 * derived arithmetically from the stored fills. Nothing is inferred from the
 * return percentage — the previous implementation did exactly that, labelling
 * any reconciled exit below +24% as "Stop Loss (-7%)" even when the trade
 * closed at -2.77%, and attributing exits to a +25% profit target that the bot
 * no longer has. Where a number was never captured, the panel says so by name
 * instead of leaving a gap that reads as a zero.
 */
export default function ExitDetailPanel({ trade }) {
  const cls = classifyExit(trade.exit_reason);
  const math = deriveTradeMath(trade);
  const facts = extractReasonFacts(trade.exit_reason);
  const missing = unrecordedFields(trade, cls);

  const Icon = EXECUTOR_ICON[cls.executor.key] || HelpCircle;
  const accent = EXECUTOR_COLOR[cls.executor.key];
  const pnlColor = math.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)';

  return (
    <div data-exit-detail={trade.ticker} style={{
      padding: '1.1rem 1.25rem 1.35rem',
      background: 'rgba(139, 92, 246, 0.035)',
      display: 'flex', flexDirection: 'column', gap: '0.85rem',
    }}>

      {/* Headline: who sold it, and under which rule */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem' }}>
        <div style={{
          width: 34, height: 34, borderRadius: '8px', flexShrink: 0,
          background: `${accent}1f`, color: accent,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Icon size={17} />
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
            <span style={{
              fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '1rem',
            }}>
              {trade.ticker} — {cls.label}
            </span>
            <span style={{
              fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.06em',
              textTransform: 'uppercase', padding: '0.15rem 0.45rem',
              borderRadius: '4px', background: `${accent}1f`, color: accent,
            }}>
              Sold by {cls.executor.label}
            </span>
          </div>
          <div style={{
            fontSize: '0.8rem', color: 'var(--text-secondary)',
            marginTop: '0.3rem', lineHeight: 1.5,
          }}>
            {cls.what}
          </div>
          <div style={{
            fontSize: '0.75rem', color: 'var(--text-muted)',
            marginTop: '0.25rem', lineHeight: 1.5,
          }}>
            {cls.executor.blurb}
          </div>
        </div>
      </div>

      {cls.priceUncertain && (
        <div style={{
          display: 'flex', gap: '0.5rem', alignItems: 'flex-start',
          background: 'rgba(244, 63, 94, 0.08)',
          border: '1px solid rgba(244, 63, 94, 0.3)',
          borderRadius: '8px', padding: '0.6rem 0.8rem',
        }}>
          <AlertTriangle size={15} style={{ color: '#f43f5e', flexShrink: 0, marginTop: 1 }} />
          <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            <strong style={{ color: '#f43f5e' }}>Price is unverified.</strong>{' '}
            No fill was found in the TWS session, so the recorded sell price is an
            FMP quote used as a placeholder. If the fill happened on an earlier day
            this is the wrong day&apos;s price, and the P&amp;L below is wrong with it.
            Check the IBKR transaction history and correct the record.
          </div>
        </div>
      )}

      {/* The money */}
      <Section title="Trade economics">
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(115px, 1fr))',
          gap: '0.85rem 1rem',
        }}>
          <Field label="Shares" value={math.shares ?? '—'} />
          <Field label="Buy price" value={money(math.buyPrice)} />
          <Field label="Sell price" value={money(math.sellPrice)} />
          <Field
            label="Move per share"
            value={math.perShare === null ? '—' : `${math.perShare >= 0 ? '+' : ''}${money(math.perShare)}`}
            color={pnlColor}
          />
          <Field label="Cost basis" value={money(math.costBasis)} />
          <Field label="Proceeds" value={money(math.proceeds)} />
          <Field
            label="Realised P&L"
            value={`${math.pnl >= 0 ? '+' : ''}${money(math.pnl)}`}
            color={pnlColor}
          />
          <Field label="Return" value={pct(math.returnPct)} color={pnlColor} />
        </div>
      </Section>

      {/* Timeline */}
      <Section title="Timeline">
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: '0.85rem 1rem',
        }}>
          <Field
            label="Bought"
            value={
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                <Calendar size={11} style={{ color: 'var(--text-muted)' }} />
                {trade.buy_date ? new Date(trade.buy_date).toLocaleString() : '—'}
              </span>
            }
            mono={false}
          />
          <Field
            label="Sold"
            value={
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                <Calendar size={11} style={{ color: 'var(--text-muted)' }} />
                {trade.sell_date ? new Date(trade.sell_date).toLocaleString() : 'Not recorded'}
              </span>
            }
            mono={false}
          />
          <Field
            label="Held for"
            value={math.days === null ? 'Unknown' : `${math.days} day${math.days === 1 ? '' : 's'}`}
          />
        </div>
      </Section>

      {/* Numbers the rule itself recorded */}
      {facts.length > 0 && (
        <Section title={`What the ${cls.label} rule recorded`}>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
            gap: '0.85rem 1rem',
          }}>
            {facts.map((f) => (
              <Field key={f.label} label={f.label} value={formatFact(f)} />
            ))}
          </div>
        </Section>
      )}

      {/* Entry rationale, for context on why it was bought at all */}
      {trade.buy_reason && (
        <Section title="Why it was bought">
          <div style={{
            fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.55,
          }}>
            {trade.buy_reason}
          </div>
        </Section>
      )}

      {/* Verbatim record */}
      <Section title="Exit reason as stored">
        <div style={{
          display: 'flex', gap: '0.5rem', alignItems: 'flex-start',
          fontSize: '0.78rem', color: 'var(--text-secondary)',
          fontFamily: 'var(--font-mono, monospace)', lineHeight: 1.55,
          wordBreak: 'break-word',
        }}>
          <Quote size={13} style={{ color: 'var(--text-muted)', flexShrink: 0, marginTop: 2 }} />
          <span>{cls.raw || 'No reason recorded.'}</span>
        </div>
      </Section>

      {/* Explicit statement of what was never captured */}
      {missing.length > 0 && (
        <Section
          title="Not recorded for this exit"
          style={{ borderColor: 'rgba(245, 158, 11, 0.28)', background: 'rgba(245, 158, 11, 0.05)' }}
        >
          <ul style={{
            margin: 0, paddingLeft: '1.1rem',
            fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.6,
          }}>
            {missing.map((m) => <li key={m}>{m}</li>)}
          </ul>
          <div style={{
            marginTop: '0.6rem', fontSize: '0.72rem', color: 'var(--text-muted)',
            lineHeight: 1.55, display: 'flex', gap: '0.4rem', alignItems: 'flex-start',
          }}>
            <Hash size={12} style={{ flexShrink: 0, marginTop: 2 }} />
            <span>
              These values are absent from the database, not hidden by this screen.
              They are shown as missing rather than back-filled with a guess.
            </span>
          </div>
        </Section>
      )}
    </div>
  );
}
