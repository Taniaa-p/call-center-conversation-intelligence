import { forwardRef } from 'react'
import { Chip, Redacted } from './ui.jsx'

// Redacted conversation, customer on the left, agent on the right.
// turns: [{turn_id, speaker, text, score, flags, note}], all already redacted by the service.
const Transcript = forwardRef(function Transcript({ turns, focus, animate = false, scroll = false }, ref) {
  return (
    <ol className={`convo ${scroll ? 'scroll' : ''}`} ref={ref}>
      {turns.map((t) => (
        <li key={t.turn_id} id={`turn-${t.turn_id}`}
          className={`bubble ${t.speaker} ${focus === t.turn_id ? 'focus' : ''} ${animate ? 'enter' : ''}`}>
          <span className="bubble-who"><span className="mono">#{t.turn_id}</span><span>{t.speaker}</span></span>
          <span className="bubble-text"><Redacted text={t.text} /></span>
          {(t.score != null || t.flags?.length > 0 || t.note) && (
            <span className="bubble-meta">
              {t.score != null && <span className={`sent-dot ${sentLabel(t.score)}`}>{sentLabel(t.score)} {t.score.toFixed(2)}</span>}
              {(t.flags || []).map((f) => (
                <Chip key={f.rule_id} tone={f.kind === 'prohibited_promise' ? 'bad' : 'warn'}
                  title={`rule matched: "${f.quote}"`}>⚑ {f.rule_id.replaceAll('_', ' ')}</Chip>
              ))}
              {t.note && <span>{t.note}</span>}
            </span>
          )}
        </li>
      ))}
    </ol>
  )
})

export const sentLabel = (s) => (s > 0.15 ? 'positive' : s < -0.15 ? 'negative' : 'neutral')

export default Transcript
