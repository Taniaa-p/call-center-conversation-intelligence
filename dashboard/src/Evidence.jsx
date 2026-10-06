import { Redacted } from './ui.jsx'

// Quoted evidence. "#4" jumps to that turn; the check mark means the grounding validator
// found the quote verbatim in that turn (✗ = not found, so the item went to human review).
export default function Evidence({ items, onTurn }) {
  if (!items || items.length === 0) return <div className="muted small">no evidence cited</div>
  return (
    <div className="evidence">
      {items.map((e, i) => (
        <div key={i} className="quote">
          <button className="turn" onClick={() => onTurn && onTurn(e.turn_id)} title="jump to this turn">#{e.turn_id}</button>
          <q><Redacted text={e.quote} /></q>
          <span className={`check ${e.verified ? 'ok' : 'no'}`}
            title={e.verified ? 'quote found in the cited turn' : 'quote NOT found in the cited turn'}>
            {e.verified ? '✓ grounded' : '✗ unverified'}
          </span>
        </div>
      ))}
    </div>
  )
}
