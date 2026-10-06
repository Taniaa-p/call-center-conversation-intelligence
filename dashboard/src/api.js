// Same origin when served by FastAPI at /ui; proxied to :8000 by Vite in dev.
const BASE = import.meta.env.DEV ? '/api' : ''

// Optional API key (when the server sets API_KEY). Stored per browser; storage may be unavailable.
export function getKey() {
  try { return localStorage.getItem('convo_api_key') || '' } catch { return '' }
}
export function setKey(k) {
  try { localStorage.setItem('convo_api_key', k) } catch { /* ignore */ }
}
const headers = (extra = {}) => (getKey() ? { ...extra, 'X-API-Key': getKey() } : extra)

export async function get(path) {
  const r = await fetch(BASE + path, { headers: headers() })
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

export async function post(path, body) {
  const r = await fetch(BASE + path, {
    method: 'POST', headers: headers({ 'Content-Type': 'application/json' }), body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

export function wsUrl(path) {
  const key = getKey() ? `${path.includes('?') ? '&' : '?'}api_key=${encodeURIComponent(getKey())}` : ''
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}${key}`
}

export const pct = (x) => (x == null ? '–' : `${Math.round(Number(x) * 100)}%`)
export const num = (x, d = 1) => (x == null ? '–' : Number(x).toFixed(d))
