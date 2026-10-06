// Small shared building blocks used by every page. No UI library: plain React + CSS.

const ICONS = {
  overview: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z',
  live: 'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zm7 9a7 7 0 0 1-14 0H3a9 9 0 0 0 8 8.94V23h2v-2.06A9 9 0 0 0 21 12h-2z',
  calls: 'M4 4h16v12H5.17L4 17.17V4zm0-2a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H4zm2 4h12v2H6V6zm0 4h9v2H6v-2z',
  analyze: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm4 18H6V4h7v5h5v11zM8 12h8v2H8v-2zm0 4h8v2H8v-2z',
  people: 'M16 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm-8 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5C15 14.17 10.33 13 8 13zm8 0c-.29 0-.62.02-.97.05A4.22 4.22 0 0 1 17 16.5V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z',
  review: 'M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z',
  ops: 'M3 13h2v8H3v-8zm4-6h2v14H7V7zm4 4h2v10h-2V11zm4-8h2v18h-2V3zm4 10h2v8h-2v-8z',
  logo: 'M4 6h16v2H4V6zm0 5h10v2H4v-2zm0 5h13v2H4v-2zm15-6 3 3-3 3v-6z',
}

export function Icon({ name, size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  )
}

export function PageHead({ title, children, right }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{children && <p>{children}</p>}</div>
      {right}
    </div>
  )
}

export function Card({ title, sub, right, className = '', children }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="card-head">
          <div><h2>{title}</h2>{sub && <div className="sub">{sub}</div>}</div>
          {right}
        </div>
      )}
      {children}
    </section>
  )
}

export function Stat({ label, value, sub }) {
  return (
    <div className="card stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  )
}

export function Chip({ tone = '', children, title }) {
  return <span className={`chip ${tone}`} title={title}>{children}</span>
}

// QA score colour bands are a display choice only (the service returns the raw %).
export const qaTone = (s) => (s == null ? 'none' : s >= 85 ? 'good' : s >= 70 ? 'warn' : 'bad')

export function Score({ value, big = false }) {
  return <span className={`score ${qaTone(value)} ${big ? 'big' : ''}`}>{value == null ? '–' : Math.round(value)}</span>
}

export function Meter({ value, max = 1, tone = '', text }) {
  const w = value == null ? 0 : Math.max(0, Math.min(1, value / max)) * 100
  return (
    <div className="meter">
      <div className="meter-track"><div className={`meter-fill ${tone}`} style={{ width: `${w}%` }} /></div>
      <span className="meter-value">{text}</span>
    </div>
  )
}

// Horizontal bars for one series: label, bar, value at the tip.
export function BarList({ rows }) {
  const max = Math.max(...rows.map((r) => r.value), 1)
  return (
    <div className="bars">
      {rows.map((r) => (
        <div key={r.label} className="bar-row" title={`${r.label}: ${r.text}`}>
          <span className="bar-label">{r.label}</span>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${(r.value / max) * 100}%` }} /></div>
          <span className="bar-value">{r.text}</span>
        </div>
      ))}
    </div>
  )
}

const VERDICTS = {
  pass: ['good', '✓ Pass'], partial: ['warn', '◐ Partial'], fail: ['bad', '✕ Fail'],
  na: ['', '– N/A'], insufficient_evidence: ['', '? Insufficient evidence'],
}
export function Verdict({ verdict }) {
  const [tone, label] = VERDICTS[verdict] || ['', verdict]
  return <Chip tone={tone}>{label}</Chip>
}

const RESOLUTION = { resolved: ['good', '✓ Resolved'], partial: ['warn', '◐ Partly resolved'], unresolved: ['bad', '✕ Unresolved'], escalated: ['warn', '↗ Escalated'] }
export function Resolution({ value }) {
  if (!value) return <Chip>not analysed</Chip>
  const [tone, label] = RESOLUTION[value] || ['', value]
  return <Chip tone={tone}>{label}</Chip>
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>
}

// Smooth scrolling only when the user has not asked for reduced motion.
export const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// "identity_verification" -> "Identity verification"
export const pretty = (id) => (id ? (id.charAt(0).toUpperCase() + id.slice(1).replaceAll('_', ' ')).replace(/^Qa\b/, 'QA') : '')

// Highlight PII placeholders like [PHONE_1] so it is obvious the text was redacted.
export function Redacted({ text }) {
  const parts = String(text).split(/(\[[A-Z_]+_\d+\])/g)
  return <>{parts.map((p, i) => (/^\[[A-Z_]+_\d+\]$/.test(p) ? <span key={i} className="ph">{p}</span> : p))}</>
}
