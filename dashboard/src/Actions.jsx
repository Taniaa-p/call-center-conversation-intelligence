import Evidence from './Evidence.jsx'
import { Chip, Empty } from './ui.jsx'

// Follow-up actions with their lifecycle (open -> done). `flash` = ids changed by the latest
// live update, briefly highlighted so you can see the list react to the conversation.
export default function Actions({ actions, onTurn, flash = [] }) {
  if (!actions || actions.length === 0) return <Empty>No follow-up actions yet.</Empty>
  return (
    <div className="actions">
      {actions.map((a) => (
        <div key={a.action_id} className={`action ${a.status} ${flash.includes(a.action_id) ? 'flash' : ''}`}>
          <div className="action-head">
            <span className="action-id">{a.action_id}</span>
            <span className="action-text">{a.text}</span>
            {a.status === 'open' ? <Chip tone="info">○ Open</Chip> : <Chip tone="good">✓ {a.status === 'done' ? 'Done' : a.status}</Chip>}
          </div>
          <div className="action-meta">
            owner: {a.owner} · raised at turn #{a.created_turn}{a.updated_turn !== a.created_turn && <> · updated #{a.updated_turn}</>}
            {a.confidence != null && <> · confidence {Math.round(a.confidence * 100)}%</>}
            {!a.verified && <> · <span className="check no">evidence unverified</span></>}
          </div>
          <Evidence items={a.evidence} onTurn={onTurn} />
        </div>
      ))}
    </div>
  )
}
