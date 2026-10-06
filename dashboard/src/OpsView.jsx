import { useEffect, useState } from 'react'
import { get, num, pct } from './api.js'
import { Card, Chip, Empty, Meter, PageHead, Stat, pretty } from './ui.jsx'

// System health and model quality in one place. Prometheus scrapes /metrics for alerting;
// this page reads the same facts from Postgres views.
export default function OpsView() {
  const [health, setHealth] = useState(null)
  const [llm, setLlm] = useState(null)
  const [drift, setDrift] = useState([])
  const [review, setReview] = useState([])
  const [judge, setJudge] = useState([])
  useEffect(() => {
    get('/health?deep=true').then(setHealth).catch((e) => setHealth({ status: String(e) }))
    get('/monitoring/llm').then(setLlm).catch(() => {})
    get('/monitoring/drift?recent_days=1&baseline_days=7').then(setDrift).catch(() => {})
    get('/monitoring/review').then(setReview).catch(() => {})
    get('/monitoring/judge').then(setJudge).catch(() => {})
  }, [])

  const reviewed = review.reduce((a, r) => a + (r.n_items - r.n_pending), 0)
  const overturned = review.reduce((a, r) => a + r.n_overturned, 0)

  return (
    <>
      <PageHead title="Monitoring">Service health, LLM cost and latency, and quality signals that keep working after launch.
        Prometheus scrapes <code>/metrics</code> (API) and <code>:9101</code> (worker); alert rules are in <code>ops/alerts.yml</code>.</PageHead>
      <div className="stack">
        <Card title="Service health" sub={health?.config_version ? `config ${health.config_version}` : ''}>
          {health ? (
            <div className="row">
              {['db', 'redis', 'llm'].map((k) => (
                <Chip key={k} tone={health[k] === 'ok' ? 'good' : 'bad'}>{health[k] === 'ok' ? '✓' : '✕'} {k.toUpperCase()}: {String(health[k] ?? '–')}</Chip>
              ))}
            </div>
          ) : <Empty>Checking…</Empty>}
        </Card>

        {llm && (
          <div className="stats">
            <Stat label="Calls analysed with an LLM" value={llm.summary.calls_with_llm} />
            <Stat label="Average cost per call" value={llm.summary.avg_cost_per_call_usd != null ? `$${llm.summary.avg_cost_per_call_usd.toFixed(4)}` : '–'} sub="live updates + end-of-call" />
            <Stat label="Total LLM cost" value={`$${Number(llm.summary.total_cost_usd).toFixed(3)}`} sub={`${Number(llm.summary.total_tokens).toLocaleString()} tokens`} />
            <Stat label="Human overturn rate" value={reviewed ? pct(overturned / reviewed) : '–'} sub={`${reviewed} reviewed items`} />
          </div>
        )}

        {llm && (
          <Card title="LLM latency and cost by task" sub="p50 / p95 exclude cache hits">
            <div className="table-wrap"><table>
              <thead><tr><th>Day</th><th>Task</th><th>Model</th><th className="r">Requests</th><th className="r">p50</th><th className="r">p95</th><th className="r">Cost</th></tr></thead>
              <tbody>{llm.daily.map((d, i) => (
                <tr key={i}><td className="muted">{String(d.day)}</td><td><strong>{pretty(d.task)}</strong></td><td><code>{d.model}</code></td>
                  <td className="r">{d.n_calls}</td><td className="r">{ms(d.p50_ms)}</td><td className="r">{ms(d.p95_ms)}</td><td className="r">${Number(d.cost_usd).toFixed(4)}</td></tr>))}
              </tbody>
            </table></div>
          </Card>
        )}

        <div className="grid cols-2" style={{ alignItems: 'start' }}>
          <Card title="Call-reason drift" sub="share in the last day vs the previous 7 days · alert above 15 points">
            {drift.length === 0 ? <Empty>No calls in the window.</Empty> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Reason</th><th>Recent</th><th>Baseline</th><th className="r">Shift</th></tr></thead>
                <tbody>{drift.map((d) => (
                  <tr key={d.reason}><td>{pretty(d.reason)}</td><td><Meter value={d.recent_share} text={pct(d.recent_share)} /></td>
                    <td><Meter value={d.baseline_share} text={pct(d.baseline_share)} /></td>
                    <td className="r">{d.alert ? <Chip tone="warn">⚠ {pct(d.shift)}</Chip> : pct(d.shift)}</td></tr>))}
                </tbody>
              </table></div>
            )}
          </Card>
          <Card title="Online LLM judge" sub="a different model re-checks a 5% sample blind; disagreements go to humans">
            {judge.length === 0 ? <Empty>No sampled calls judged yet.</Empty> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Day</th><th>Judge model</th><th className="r">Calls</th><th className="r">Faithfulness</th><th className="r">Flagged claims</th></tr></thead>
                <tbody>{judge.map((j, i) => <tr key={i}><td className="muted">{String(j.day)}</td><td><code>{j.model}</code></td><td className="r">{j.n_judged}</td><td className="r">{pct(j.avg_faithfulness)}</td><td className="r">{pct(j.unsupported_rate)}</td></tr>)}</tbody>
              </table></div>
            )}
          </Card>
        </div>

        <Card title="Human review agreement" sub="how often reviewers overturn the model, by item type and reason">
          {review.length === 0 ? <Empty>No review items.</Empty> : (
            <div className="table-wrap"><table>
              <thead><tr><th>Item type</th><th>Reason</th><th className="r">Items</th><th className="r">Pending</th><th className="r">Overturned</th><th className="r">Overturn rate</th></tr></thead>
              <tbody>{review.map((r, i) => <tr key={i}><td>{pretty(r.item_type)}</td><td>{pretty(r.reason)}</td><td className="r">{r.n_items}</td><td className="r">{r.n_pending}</td><td className="r">{r.n_overturned}</td><td className="r">{pct(r.overturn_rate)}</td></tr>)}</tbody>
            </table></div>
          )}
        </Card>
      </div>
    </>
  )
}

const ms = (x) => (x == null ? '–' : x >= 1000 ? `${(x / 1000).toFixed(1)} s` : `${num(x, 0)} ms`)
