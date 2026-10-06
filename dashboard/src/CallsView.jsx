import { useEffect, useState } from 'react'
import { get } from './api.js'
import { Card, Chip, Empty, PageHead, Score, pretty } from './ui.jsx'
import CallDetail from './CallDetail.jsx'

const FILTERS = {
  all: ['All', () => true],
  violations: ['Violations', (c) => c.n_violations > 0],
  churn: ['Churn risk', (c) => c.churn_flag],
  open: ['Not resolved', (c) => c.resolution && c.resolution !== 'resolved'],
  chat: ['Chat', (c) => c.channel === 'chat'],
}

export default function CallsView({ selected, onSelect }) {
  const [calls, setCalls] = useState([])
  const [text, setText] = useState('')
  const [filter, setFilter] = useState('all')
  const [showDemo, setShowDemo] = useState(false)
  useEffect(() => { get(`/calls?limit=1000${showDemo ? '&include_demo=true' : ''}`).then(setCalls).catch(() => {}) }, [selected, showDemo])

  const q = text.toLowerCase()
  const shown = calls.filter(FILTERS[filter][1]).filter((c) => !q || [c.call_id, c.agent_id, c.team_id, ...(c.reasons || [])].some((v) => v?.toLowerCase().includes(q)))

  return (
    <>
      <PageHead title="Calls">Every analysed conversation. Open one to see why each score was given, with the quoted turns.</PageHead>
      <div className="grid cols-calls">
        <Card className="sticky">
          <div className="filters">
            <input placeholder="Search call, agent, team or reason" value={text} onChange={(e) => setText(e.target.value)} />
            <div className="row">
              {Object.entries(FILTERS).map(([k, [label, f]]) => (
                <button key={k} className={`btn sm ${filter === k ? 'primary' : ''}`} onClick={() => setFilter(k)}>
                  {label} <span className="num">{calls.filter(f).length}</span>
                </button>
              ))}
            </div>
            <label className="muted small"><input type="checkbox" checked={showDemo} onChange={(e) => setShowDemo(e.target.checked)} /> Show demo runs</label>
          </div>
          <div className="list calls-list">
            {shown.map((c) => (
              <button key={c.call_id} className={`list-item ${selected === c.call_id ? 'sel' : ''}`} onClick={() => onSelect(c.call_id)}>
                <Score value={c.qa_score_pct} />
                <div className="grow">
                  <div className="title">{c.call_id} <span className="muted small">· {c.channel}</span></div>
                  <div className="meta">{c.agent_id} · {c.status === 'analyzed' ? (c.reasons || []).map(pretty).join(', ') : c.status}</div>
                </div>
                {c.is_demo && <Chip title="live replay or pasted transcript: not in roll-ups">demo</Chip>}
                {c.n_violations > 0 && <Chip tone="bad" title="compliance violations">⚑ {c.n_violations}</Chip>}
                {c.churn_flag && <Chip tone="warn" title="churn risk">⚠</Chip>}
              </button>
            ))}
            {shown.length === 0 && <Empty>No calls match.</Empty>}
          </div>
        </Card>
        <div>{selected ? <CallDetail callId={selected} /> : <Card><Empty>Select a call on the left.</Empty></Card>}</div>
      </div>
    </>
  )
}
