import { useEffect, useState } from 'react'
import { get, post } from './api.js'
import { Card, Chip, Empty, PageHead, Verdict, pretty } from './ui.jsx'

const REASONS = {
  unverified_evidence: 'quote not found in the cited turn',
  low_confidence: 'model confidence below threshold',
  rule_llm_disagreement: 'policy rule and LLM disagree',
  analysis_failed: 'a pipeline stage failed',
  invalid_na: 'N/A used where it is not allowed',
  missing_item: 'model skipped this item',
  verdict_not_on_scale: 'verdict not allowed for this item',
}

// Items land here instead of silently counting: humans accept or overturn them, and the
// overturn rate is monitored as a quality signal.
export default function ReviewView({ onOpenCall, onChange }) {
  const [items, setItems] = useState(null)
  const [status, setStatus] = useState('pending')
  const load = () => get(`/review?status=${status}&limit=500`).then((r) => {
    setItems(r)
    if (status === 'pending') onChange?.(r.length)
  }).catch(() => setItems([]))
  useEffect(() => { setItems(null); load() }, [status])

  async function decide(id, decision) {
    await post(`/review/${id}`, { status: decision, reviewer: 'dashboard' })
    load()
  }

  return (
    <>
      <PageHead title="Review queue" right={
        <div className="seg">{['pending', 'accepted', 'overturned'].map((s) => <button key={s} className={status === s ? 'on' : ''} onClick={() => setStatus(s)}>{pretty(s)}</button>)}</div>}>
        Anything the system is not sure about comes here instead of silently counting: ungrounded quotes, low confidence,
        rule vs LLM disagreement, failed stages.
      </PageHead>
      <Card>
        {items === null && <Empty>Loading…</Empty>}
        {items?.length === 0 && <Empty>Nothing {status}.</Empty>}
        {items?.map((r) => (
          <div key={r.review_id} className="review">
            <div className="stack" style={{ gap: 6 }}>
              <div className="row">
                <strong>{pretty(r.item_type)}: {pretty(r.item_ref)}</strong>
                <Chip tone="warn">{REASONS[r.reason] || r.reason}</Chip>
                {r.payload.verdict && <Verdict verdict={r.payload.verdict} />}
                <button className="link small" onClick={() => onOpenCall(r.call_id)}>{r.call_id} →</button>
              </div>
              {(r.payload.rationale || r.payload.error) && <div className="qa-rationale">{r.payload.rationale || r.payload.error}</div>}
              {(r.payload.evidence || []).map((e, i) => (
                <div key={i} className="quote"><span className="turn">#{e.turn_id}</span><q>{e.quote}</q>
                  <span className={`check ${e.verified ? 'ok' : 'no'}`}>{e.verified ? '✓ grounded' : '✗ unverified'}</span></div>
              ))}
              {r.reviewer && <div className="muted small">{r.status} by {r.reviewer}</div>}
            </div>
            {r.status === 'pending' && (
              <div className="row" style={{ alignItems: 'flex-start' }}>
                <button className="btn sm" onClick={() => decide(r.review_id, 'accepted')} title="the model was right">✓ Accept</button>
                <button className="btn sm danger" onClick={() => decide(r.review_id, 'overturned')} title="the model was wrong">✕ Overturn</button>
              </div>
            )}
          </div>
        ))}
      </Card>
    </>
  )
}
