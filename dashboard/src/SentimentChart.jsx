import { useState } from 'react'
import { Empty } from './ui.jsx'

// Customer sentiment per customer turn (-1..+1). One series, so no legend: the card title names it.
// Dots use the diverging pair (red negative, blue positive, gray neutral); the tooltip always
// gives the label too, so colour never carries the meaning alone.
export default function SentimentChart({ points, height = 140, onTurn }) {
  const [hover, setHover] = useState(null)
  if (!points || points.length === 0) return <Empty>No customer turns yet.</Empty>
  const W = 560, H = height, padL = 30, padR = 12, padT = 12, padB = 22
  const maxTurn = Math.max(...points.map((p) => p.turn_id), 2)
  const minTurn = Math.min(...points.map((p) => p.turn_id))
  const x = (t) => padL + ((t - minTurn) / Math.max(1, maxTurn - minTurn)) * (W - padL - padR)
  const y = (s) => padT + ((1 - s) / 2) * (H - padT - padB)
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.turn_id)},${y(p.score)}`).join(' ')
  const colour = (s) => (s > 0.15 ? 'var(--pos)' : s < -0.15 ? 'var(--neg)' : 'var(--neutral-mark)')
  const label = (s) => (s > 0.15 ? 'positive' : s < -0.15 ? 'negative' : 'neutral')
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Customer sentiment by turn">
        {[1, 0, -1].map((v) => (
          <g key={v}>
            <line x1={padL} x2={W - padR} y1={y(v)} y2={y(v)} className={v === 0 ? 'zero' : 'grid'} />
            <text x={padL - 8} y={y(v) + 3} className="tick" textAnchor="end">{v > 0 ? '+1' : v}</text>
          </g>
        ))}
        <path d={path} className="line" />
        {points.map((p) => (
          <g key={p.turn_id} onMouseEnter={() => setHover(p)} onMouseLeave={() => setHover(null)}
            onClick={() => onTurn && onTurn(p.turn_id)} style={{ cursor: onTurn ? 'pointer' : 'default' }}>
            <circle cx={x(p.turn_id)} cy={y(p.score)} r={13} fill="transparent" />
            <circle cx={x(p.turn_id)} cy={y(p.score)} r={hover === p ? 6 : 4.5} fill={colour(p.score)} className="dot" />
            <text x={x(p.turn_id)} y={H - 4} className="tick" textAnchor="middle">#{p.turn_id}</text>
          </g>
        ))}
      </svg>
      {hover && (
        <div className="tooltip" style={{ left: `${(x(hover.turn_id) / W) * 100}%`, top: `${(y(hover.score) / H) * 100}%` }}>
          turn #{hover.turn_id} · {label(hover.score)} · {hover.score.toFixed(2)}
        </div>
      )}
    </div>
  )
}
