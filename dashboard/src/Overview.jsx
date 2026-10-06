import { useEffect, useState } from 'react'
import { get, pct } from './api.js'
import { BarList, Card, Chip, Empty, Meter, PageHead, Score, Stat, pretty, qaTone } from './ui.jsx'

// The landing page: what a contact-center lead wants to see first. Everything is computed
// in the browser from the same REST endpoints the other pages use (no extra backend code).
export default function Overview({ onOpenCall, go }) {
  const [calls, setCalls] = useState(null)
  const [teams, setTeams] = useState([])
  const [pending, setPending] = useState(0)
  const [cost, setCost] = useState(null)

  useEffect(() => {
    get('/calls?limit=1000').then(setCalls).catch(() => setCalls([]))
    get('/teams').then(setTeams).catch(() => {})
    get('/review?status=pending&limit=500').then((r) => setPending(r.length)).catch(() => {})
    get('/monitoring/llm').then((d) => setCost(d.summary)).catch(() => {})
  }, [])

  if (calls === null) return <Empty>Loading…</Empty>
  const done = calls.filter((c) => c.status === 'analyzed')
  if (done.length === 0) {
    return (
      <>
        <PageHead title="Overview" />
        <Card><Empty>No analysed calls yet. Run <code>make demo</code> (instant) or <code>make seed</code>, or
          try <button className="link" onClick={() => go('analyze')}>analysing a transcript</button>.</Empty></Card>
      </>
    )
  }

  const scored = done.filter((c) => c.qa_score_pct != null)
  const avgQa = scored.reduce((s, c) => s + c.qa_score_pct, 0) / Math.max(1, scored.length)
  const share = (f) => done.filter(f).length / done.length
  const withViolation = done.filter((c) => c.n_violations > 0)
  const churn = done.filter((c) => c.churn_flag)

  const reasonCounts = countBy(done.flatMap((c) => c.reasons || []))
  const resolutionCounts = countBy(done.map((c) => c.resolution).filter(Boolean))
  const toRows = (counts) => Object.entries(counts).sort((a, b) => b[1] - a[1])
    .map(([k, n]) => ({ label: pretty(k), value: n, text: `${pct(n / done.length)} · ${n}` }))

  const attention = done.filter((c) => c.n_violations > 0 || c.churn_flag)
    .sort((a, b) => (a.qa_score_pct ?? 0) - (b.qa_score_pct ?? 0)).slice(0, 7)

  return (
    <>
      <PageHead title="Overview">
        Every conversation is analysed: summary, call reasons, sentiment, resolution, churn risk, live follow-up actions
        and QA scoring with quoted evidence.
      </PageHead>

      <div className="stack">
        <Card className="hero">
          <div className="hero-value">100%</div>
          <div className="hero-text">
            of <strong>{done.length} conversations</strong> QA-scored automatically, against the ~2% a quality team
            can review by hand. Humans only see what the system flags: <strong>{pending} item{pending === 1 ? '' : 's'}</strong> in the review queue.
          </div>
        </Card>

        <div className="stats">
          <Stat label="Average QA score" value={`${Math.round(avgQa)} / 100`} sub={`over ${scored.length} scored calls`} />
          <Stat label="Calls with a compliance violation" value={pct(share((c) => c.n_violations > 0))} sub={`${withViolation.length} calls, each with quoted evidence`} />
          <Stat label="Churn risk flagged" value={pct(churn.length / done.length)} sub={`${churn.length} customers to call back`} />
          <Stat label="Resolved on the call" value={pct(share((c) => c.resolution === 'resolved'))} sub="first-call resolution" />
          <Stat label="LLM cost per call" value={cost?.avg_cost_per_call_usd != null ? `$${cost.avg_cost_per_call_usd.toFixed(4)}` : '–'} sub="measured tokens × list price" />
        </div>

        <div className="grid cols-2">
          <Card title="Why customers call" sub="share of calls; a call can have several reasons">
            <BarList rows={toRows(reasonCounts)} />
          </Card>
          <Card title="How calls end" sub="resolution status at the end of the call">
            <BarList rows={toRows(resolutionCounts)} />
          </Card>
        </div>

        <div className="grid cols-2">
          <Card title="Needs attention" sub="violations or churn risk, lowest QA first"
            right={<button className="link" onClick={() => go('calls')}>All calls →</button>}>
            <div className="list">
              {attention.map((c) => (
                <button key={c.call_id} className="list-item" onClick={() => onOpenCall(c.call_id)}>
                  <Score value={c.qa_score_pct} />
                  <div className="grow">
                    <div className="title">{c.call_id}</div>
                    <div className="meta">{c.agent_id} · {c.team_id} · {(c.reasons || []).map(pretty).join(', ')}</div>
                  </div>
                  {c.n_violations > 0 && <Chip tone="bad">⚑ {c.n_violations} violation{c.n_violations > 1 ? 's' : ''}</Chip>}
                  {c.churn_flag && <Chip tone="warn">⚠ churn</Chip>}
                </button>
              ))}
            </div>
          </Card>
          <Card title="Teams" sub="QA, violations and churn per team"
            right={<button className="link" onClick={() => go('people')}>Agents →</button>}>
            <div className="table-wrap"><table>
              <thead><tr><th>Team</th><th className="r">Calls</th><th>Avg QA</th><th>Calls with violation</th><th>Churn</th></tr></thead>
              <tbody>
                {teams.map((t) => (
                  <tr key={t.team_id}>
                    <td style={{ whiteSpace: 'nowrap' }}><strong>{pretty(t.team_id)}</strong></td>
                    <td className="r">{t.n_calls}</td>
                    <td><Meter value={t.avg_qa_pct} max={100} tone={qaTone(t.avg_qa_pct)} text={Math.round(t.avg_qa_pct)} /></td>
                    <td><Meter value={t.violation_rate} text={pct(t.violation_rate)} /></td>
                    <td><Meter value={t.churn_rate} text={pct(t.churn_rate)} /></td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          </Card>
        </div>
      </div>
    </>
  )
}

function countBy(values) {
  const out = {}
  for (const v of values) out[v] = (out[v] || 0) + 1
  return out
}
