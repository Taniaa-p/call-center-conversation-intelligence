import { useEffect, useState } from 'react'
import { get, pct } from './api.js'
import Evidence from './Evidence.jsx'
import SentimentChart from './SentimentChart.jsx'
import Actions from './Actions.jsx'
import Transcript from './Transcript.jsx'
import { Card, Chip, Empty, Resolution, Score, Verdict, pretty, reducedMotion } from './ui.jsx'

// One call, explained: every verdict shows its rationale, confidence and quoted evidence.
// Clicking a quote's turn number scrolls the transcript to that turn.
export default function CallDetail({ callId }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [focus, setFocus] = useState(null)
  useEffect(() => {
    setData(null); setError(null); setFocus(null)
    get(`/calls/${encodeURIComponent(callId)}`).then(setData).catch((e) => setError(String(e)))
  }, [callId])

  if (error) return <Card><Empty>Could not load {callId}: {error}</Empty></Card>
  if (!data) return <Card><Empty>Loading…</Empty></Card>
  const a = data.analysis?.result
  const f = a?.final
  const goTurn = (id) => { setFocus(id); document.getElementById(`turn-${id}`)?.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'center' }) }
  const turns = data.turns.map((t) => ({ ...t, score: t.sentiment, flags: t.rule_flags }))

  return (
    <div className="stack">
      <Card>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h1 className="mono" style={{ fontSize: 20 }}>{callId}</h1>
            <div className="muted small" style={{ marginTop: 4 }}>
              {data.call.agent_id} · {pretty(data.call.team_id)} · {data.call.channel} · {data.turns.length} turns
            </div>
          </div>
          {a?.qa && <div style={{ textAlign: 'right' }}><Score value={a.qa.score_pct} big /><div className="muted small" style={{ marginTop: 4 }}>QA score</div></div>}
        </div>
        {!a && <p className="muted" style={{ marginTop: 12 }}>Not analysed yet (status: {data.call.status}).</p>}
        {a && !f && <div className="notice" style={{ marginTop: 12 }}>The end-of-call analysis failed (LLM unavailable?). See the review queue; the worker retries automatically.</div>}
        {f && (
          <div className="stack" style={{ marginTop: 16 }}>
            <p className="summary">{f.summary}</p>
            <div className="facts">
              <Fact label="Call reasons">{f.reasons.map((r) => <Chip key={r.label} tone="info" title={`confidence ${pct(r.confidence)}`}>{pretty(r.label)}</Chip>)}</Fact>
              <Fact label="Resolution"><Resolution value={f.resolution} /></Fact>
              <Fact label="Churn risk">{f.churn_flag ? <Chip tone="warn">⚠ Flagged · {pct(f.churn_score)}</Chip> : <Chip tone="good">✓ Low · {pct(f.churn_score)}</Chip>}</Fact>
              <Fact label="Customer sentiment">
                <span className="num">{fmt(a.sentiment.start)} → {fmt(a.sentiment.end)}</span>
                <Chip tone={a.sentiment.direction === 'improved' ? 'good' : a.sentiment.direction === 'worsened' ? 'bad' : ''}>{a.sentiment.direction}</Chip>
              </Fact>
            </div>
          </div>
        )}
      </Card>

      {a?.qa?.violations?.length > 0 && (
        <Card title="Compliance violations" sub="failed critical checklist items and policy-rule hits, with the turns that prove them">
          {a.qa.violations.map((v, i) => (
            <div key={i} className="violation">
              <div className="violation-title">⚑ {pretty(v.item_id)} <Chip tone="bad">{v.severity} severity</Chip>
                <Chip tone="outline" title="rule = regex in policy.yaml, llm = QA model, rule+llm = both">detected by {v.source}</Chip>
                {!v.verified && <Chip tone="warn">unverified · sent to review</Chip>}</div>
              <Evidence items={v.evidence} onTurn={goTurn} />
            </div>
          ))}
        </Card>
      )}

      {a?.qa && (
        <Card title="QA checklist" sub="LLM gives a verdict per item with evidence; code checks the quotes and computes the weighted score">
          {a.qa.items.map((it) => (
            <div key={it.item_id} className="qa-item">
              <div>
                <div className="qa-name">{pretty(it.item_id)}</div>
                <div className="qa-tags">
                  <Verdict verdict={it.verdict} />
                  {it.critical && <Chip tone="outline">critical</Chip>}
                </div>
                <div className="muted small" style={{ marginTop: 6 }}>weight {it.weight} · confidence {pct(it.confidence)}</div>
              </div>
              <div>
                <div className="qa-rationale">{it.rationale}</div>
                <Evidence items={it.evidence} onTurn={goTurn} />
              </div>
            </div>
          ))}
        </Card>
      )}

      {f && (
        <Card title="Why the analysis says this" sub="evidence for reasons, resolution and churn">
          {f.reasons.map((r) => <Why key={r.label} title={`Reason: ${pretty(r.label)}`} conf={r.confidence} text={r.rationale} ev={r.evidence} onTurn={goTurn} />)}
          <Why title={`Resolution: ${f.resolution}`} conf={f.resolution_confidence} text={f.resolution_rationale} ev={f.resolution_evidence} onTurn={goTurn} />
          <Why title={`Churn: ${f.churn_flag ? 'flagged' : 'not flagged'}`} text={f.churn_rationale} ev={f.churn_evidence} onTurn={goTurn} />
        </Card>
      )}

      <div className="grid cols-2">
        <Card title="Customer sentiment by turn" sub="local scorer, every customer turn">
          {a ? <SentimentChart points={a.sentiment.points} onTurn={goTurn} /> : <Empty>Not analysed yet.</Empty>}
        </Card>
        <Card title="Follow-up actions" sub="built live, turn by turn"><Actions actions={data.actions} onTurn={goTurn} /></Card>
      </div>

      <Card title="Transcript" sub="as stored: personal data replaced by typed placeholders before any LLM call">
        <Transcript turns={turns} focus={focus} />
      </Card>

      {data.analysis && (
        <div className="audit">
          <span>model <code>{data.analysis.model_strong}</code></span>
          <span>prompts {Object.values(data.analysis.prompt_versions || {}).map((v) => <code key={v} style={{ marginRight: 4 }}>{v}</code>)}</span>
          <span>config <code>{data.analysis.config_version}</code></span>
          <span>analysed {new Date(data.analysis.created_at).toLocaleString()}</span>
        </div>
      )}
    </div>
  )
}

function Fact({ label, children }) {
  return <div className="fact"><div className="fact-label">{label}</div><div className="fact-value">{children}</div></div>
}

function Why({ title, conf, text, ev, onTurn }) {
  return (
    <div className="why">
      <div className="row"><strong>{title}</strong>{conf != null && <span className="muted small">confidence {pct(conf)}</span>}</div>
      <div className="qa-rationale" style={{ marginTop: 2 }}>{text}</div>
      <Evidence items={ev} onTurn={onTurn} />
    </div>
  )
}

const fmt = (x) => (x == null ? '–' : (x > 0 ? '+' : '') + Number(x).toFixed(2))
