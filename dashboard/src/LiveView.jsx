import { useEffect, useRef, useState } from 'react'
import { get, wsUrl } from './api.js'
import SentimentChart from './SentimentChart.jsx'
import Actions from './Actions.jsx'
import Transcript from './Transcript.jsx'
import { Card, Chip, Empty, PageHead, Score, pretty, reducedMotion } from './ui.jsx'

const SPEEDS = { fast: 0.6, normal: 1.5, slow: 3 }

// Replays a synthetic call over the live WebSocket, turn by turn, exactly like a real call would
// arrive. Two things come back per turn: the fast path (instant: redaction, sentiment, rule flags)
// and, for customer turns, a live LLM update (follow-up action deltas + rolling summary).
export default function LiveView({ onOpenCall }) {
  const [demos, setDemos] = useState([])
  const [demoId, setDemoId] = useState('')
  const [speed, setSpeed] = useState('normal')
  const [s, setS] = useState(empty())
  const ws = useRef(null)
  const convo = useRef(null)

  useEffect(() => { get('/demo/calls').then((d) => { setDemos(d); if (d[0]) setDemoId(d[0].call_id) }).catch(() => {}) }, [])
  useEffect(() => () => ws.current?.close(), [])
  useEffect(() => { convo.current?.scrollTo({ top: convo.current.scrollHeight, behavior: reducedMotion() ? 'auto' : 'smooth' }) }, [s.turns.length])

  async function start() {
    const call = await get(`/demo/calls/${demoId}`)
    const liveId = `live_${demoId}_${Date.now().toString(36)}`
    setS({ ...empty(), callId: liveId, running: true, total: call.turns.length, startedAt: Date.now() })
    const sock = new WebSocket(wsUrl(`/ws/calls/${liveId}?agent_id=${call.agent_id}&team_id=${call.team_id}&channel=${call.channel}&demo=true`))
    ws.current = sock
    sock.onmessage = (m) => setS((prev) => reduce(prev, JSON.parse(m.data)))
    sock.onclose = () => setS((prev) => ({ ...prev, running: false }))
    sock.onopen = async () => {
      for (const t of call.turns) {
        if (sock.readyState !== 1) return
        sock.send(JSON.stringify({ type: 'turn', turn_id: t.turn_id, speaker: t.speaker, text: t.text }))
        await new Promise((r) => setTimeout(r, SPEEDS[speed] * 1000))
      }
      if (sock.readyState === 1) sock.send(JSON.stringify({ type: 'end' }))
    }
  }
  const stop = () => { ws.current?.close(); setS((prev) => ({ ...prev, running: false })) }

  const demo = demos.find((d) => d.call_id === demoId)
  const turns = s.turns.map((t) => ({ ...t.turn, score: t.sentiment?.score, flags: t.flags, note: `fast path ${t.latency_ms} ms` }))
  const liveMs = s.updates.length ? Math.round(s.updates.reduce((a, b) => a + b, 0) / s.updates.length) : null

  return (
    <>
      <PageHead title="Live call" right={s.running ? <span className="live-pill">LIVE · turn {s.turns.length}/{s.total}</span> : null}>
        Stream a conversation turn by turn. Watch the fast path react instantly and the follow-up actions update after
        every customer turn.
      </PageHead>

      <Card className="toolbar">
        <label>Conversation
          <select value={demoId} onChange={(e) => setDemoId(e.target.value)} disabled={s.running}>
            {demos.map((d) => <option key={d.call_id} value={d.call_id}>{d.call_id} · {d.channel} · {d.language === 'hinglish' ? 'Hinglish' : 'English'} · {d.n_turns} turns</option>)}
          </select>
        </label>
        <div className="seg" role="group" aria-label="speed">
          {Object.keys(SPEEDS).map((k) => <button key={k} className={speed === k ? 'on' : ''} onClick={() => setSpeed(k)} disabled={s.running}>{pretty(k)}</button>)}
        </div>
        {s.running
          ? <button className="btn danger" onClick={stop}>■ Stop</button>
          : <button className="btn primary" disabled={!demoId} onClick={start}>▶ Start live call</button>}
        {demo && <span className="muted small">{demo.agent_id} · {pretty(demo.team_id)}</span>}
      </Card>

      {s.errors > 0 && (
        <div className="notice" style={{ marginBottom: 16 }}>
          The live LLM update is unavailable ({s.errors}×), probably no <code>GEMINI_API_KEY</code>. The fast path keeps working;
          missed turns are retried with the next update. Calls <code>syn_001</code>–<code>syn_015</code> replay from the
          response cache without a key.
        </div>
      )}

      <div className="grid cols-live">
        <Card title="Conversation" sub="redacted on arrival · sentiment and policy rules on every turn">
          {turns.length ? <Transcript ref={convo} turns={turns} animate scroll /> : <Empty>Press “Start live call”.</Empty>}
        </Card>

        <div className="stack">
          <div className="stats three">
            <MiniStat label="Fast path" value={s.turns.length ? `${avg(s.turns.map((t) => t.latency_ms)).toFixed(1)} ms` : '–'} sub="per turn" />
            <MiniStat label="Live LLM update" value={liveMs == null ? '–' : liveMs < 1000 ? `${liveMs} ms` : `${(liveMs / 1000).toFixed(1)} s`} sub={`avg of ${s.updates.length} updates${liveMs != null && liveMs < 50 ? ' (cached)' : ''}`} />
            <MiniStat label="Follow-up actions" value={s.actions.length} sub={`${s.actions.filter((a) => a.status === 'open').length} open`} />
          </div>

          <Card title="Follow-up actions" sub="add / update / close, deduplicated, IDs assigned by code">
            <Actions actions={s.actions} flash={s.flash} />
          </Card>

          <Card title="Rolling summary" sub={s.summaryTurn ? `updated after turn #${s.summaryTurn}` : 'updated by the live model'}>
            {s.summary ? <p>{s.summary}</p> : <Empty>Waiting for the first customer turn…</Empty>}
          </Card>

          <Card title="Customer sentiment by turn">
            <SentimentChart points={s.turns.filter((t) => t.sentiment).map((t) => t.sentiment)} height={120} />
          </Card>

          {s.final && (
            <Card title="End-of-call analysis" className="result" sub="strong model + QA checklist, run by the worker when the call ended">
              <div className="row" style={{ alignItems: 'flex-start', gap: 16 }}>
                <Score value={s.final.qa_score_pct} big />
                <div className="stack" style={{ gap: 8, flex: 1 }}>
                  <p>{s.final.summary}</p>
                  <div className="row">
                    {s.final.n_violations > 0 ? <Chip tone="bad">⚑ {s.final.n_violations} violation{s.final.n_violations > 1 ? 's' : ''}</Chip> : <Chip tone="good">✓ no violations</Chip>}
                    {s.final.n_review_items > 0 && <Chip tone="warn">{s.final.n_review_items} for human review</Chip>}
                  </div>
                  <div><button className="btn primary" onClick={() => onOpenCall(s.callId)}>Open full analysis with evidence →</button></div>
                </div>
              </div>
            </Card>
          )}

          {s.log.length > 0 && (
            <Card title="Pipeline events">
              <ul className="events">{s.log.map((e, i) => <li key={i}><span className="t">{e.t}</span>{e.text}</li>)}</ul>
            </Card>
          )}
        </div>
      </div>
    </>
  )
}

function MiniStat({ label, value, sub }) {
  return <div className="card stat"><span className="stat-label">{label}</span><span className="stat-value">{value}</span><span className="stat-sub">{sub}</span></div>
}

function empty() {
  return { callId: null, running: false, total: 0, turns: [], actions: [], flash: [], summary: '', summaryTurn: null,
    updates: [], final: null, log: [], errors: 0, startedAt: null }
}

const avg = (xs) => xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length)

// One WebSocket event -> new view state (pure function, easy to reason about).
function reduce(s, ev) {
  const at = s.startedAt ? `+${((Date.now() - s.startedAt) / 1000).toFixed(1)}s` : ''
  const log = (text) => [...s.log, { t: at, text }]
  switch (ev.type) {
    case 'turn':
      if (ev.duplicate) return { ...s, log: log(`turn #${ev.turn.turn_id} re-sent: ignored (idempotent)`) }
      return { ...s, turns: [...s.turns, ev] }
    case 'actions': {
      const changed = ev.changes.filter((c) => c.action_id).map((c) => c.action_id)
      const ops = ev.changes.map((c) => `${c.op} ${c.action_id || ''}`.trim()).join(', ') || 'no action change'
      return { ...s, actions: ev.actions, flash: changed, summary: ev.summary, summaryTurn: Math.max(...ev.turn_ids),
        updates: [...s.updates, ev.latency_ms],
        log: log(`live update for turn ${ev.turn_ids.map((t) => '#' + t).join(', ')} in ${ev.latency_ms} ms: ${ops}`) }
    }
    case 'live_delayed':
      return { ...s, log: log(`live update over the ${ev.budget_s}s budget: turns ${ev.turn_ids.join(', ')} carried to the next update`) }
    case 'live_error':
      return { ...s, errors: s.errors + 1, log: log(`live LLM unavailable for turn ${ev.turn_ids.join(', ')}: will retry with the next update`) }
    case 'ended':
      return { ...s, log: log('call ended: end-of-call analysis queued on the worker') }
    case 'analysis':
      return { ...s, final: ev, running: false, log: log('end-of-call analysis received') }
    case 'analysis_pending':
      return { ...s, running: false, log: log('analysis still running: check the Calls page in a moment') }
    default:
      return s
  }
}
