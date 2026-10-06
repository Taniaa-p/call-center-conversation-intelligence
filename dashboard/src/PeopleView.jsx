import { useEffect, useState } from 'react'
import { get, num, pct } from './api.js'
import { Card, Chip, Empty, Meter, PageHead, Score, pretty, qaTone } from './ui.jsx'

// Agent and team roll-ups (SQL views). Agents are ranked by a SHRUNK score: the raw average is
// pulled toward the team mean by 5 pseudo-calls, so 2 lucky calls cannot top the table.
export default function PeopleView({ selected, onSelect, onOpenCall }) {
  const [teams, setTeams] = useState([])
  const [agents, setAgents] = useState([])
  const [detail, setDetail] = useState(null)
  useEffect(() => { get('/teams').then(setTeams).catch(() => {}); get('/agents').then(setAgents).catch(() => {}) }, [])
  useEffect(() => {
    setDetail(null)
    if (selected) get(`/agents/${encodeURIComponent(selected)}`).then(setDetail).catch(() => {})
  }, [selected])

  return (
    <>
      <PageHead title="Agents & teams">QA results rolled up from every call, so coaching is based on all calls, not a 2% sample.</PageHead>
      <div className="stack">
        <div className="stats">
          {teams.map((t) => (
            <div key={t.team_id} className="card stat">
              <span className="stat-label">{pretty(t.team_id)} · {t.n_agents} agents · {t.n_calls} calls</span>
              <span className="stat-value"><Score value={t.avg_qa_pct} big /></span>
              <span className="stat-sub">{pct(t.violation_rate)} calls with violation · {pct(t.resolution_rate)} resolved · {pct(t.churn_rate)} churn</span>
            </div>
          ))}
        </div>

        <div className="grid cols-2" style={{ alignItems: 'start' }}>
          <Card title="Agent leaderboard" sub="ranked by shrunk QA score · click an agent for coaching detail">
            <div className="table-wrap"><table>
              <thead><tr><th>Agent</th><th className="r">Calls</th><th>Shrunk QA</th><th className="r">Raw avg</th><th>Calls with violation</th></tr></thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.agent_id} className={`click ${selected === a.agent_id ? 'sel' : ''}`} onClick={() => onSelect(a.agent_id)}>
                    <td><strong>{a.agent_id}</strong><div className="muted small">{pretty(a.team_id)}{a.low_sample && ' · fewer than 5 calls'}</div></td>
                    <td className="r">{a.n_calls}</td>
                    <td><Meter value={a.shrunk_qa_pct} max={100} tone={qaTone(a.shrunk_qa_pct)} text={num(a.shrunk_qa_pct)} /></td>
                    <td className="r muted">{num(a.avg_qa_pct)}</td>
                    <td><Meter value={a.violation_rate} text={pct(a.violation_rate)} /></td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          </Card>

          <Card title={detail ? `${detail.rollup.agent_id}: what to coach` : 'Coaching detail'}
            sub={detail ? `${pretty(detail.rollup.team_id)} · ${detail.rollup.n_calls} calls · ${detail.rollup.n_violations} violations` : 'average points per checklist item (0–1)'}>
            {!selected && <Empty>Select an agent.</Empty>}
            {selected && !detail && <Empty>Loading…</Empty>}
            {detail && (
              <div className="stack">
                <div className="bars">
                  {detail.items.map((i) => (
                    <div key={i.item_id} className="bar-row coach">
                      <span className="bar-label">{pretty(i.item_id)}</span>
                      <Meter value={i.avg_points} tone={i.avg_points == null ? '' : qaTone(i.avg_points * 100)} text={num(i.avg_points, 2)} />
                      <span className="bar-value">{i.n_fail > 0 ? <Chip tone="bad">✕ {i.n_fail} fail{i.n_fail > 1 ? 's' : ''}</Chip> : <span className="muted small">{i.n_scored} scored</span>}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <h3 style={{ marginBottom: 6 }}>Calls</h3>
                  <div className="list">
                    {detail.calls.map((c) => (
                      <button key={c.call_id} className="list-item" onClick={() => onOpenCall(c.call_id)}>
                        <Score value={c.qa_score_pct} />
                        <div className="grow"><div className="title">{c.call_id}</div><div className="meta">{c.resolution || c.status} · {(c.reasons || []).map(pretty).join(', ')}</div></div>
                        {c.n_violations > 0 && <Chip tone="bad">⚑ {c.n_violations}</Chip>}
                        {c.churn_flag && <Chip tone="warn">⚠</Chip>}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}
