import { useEffect, useRef, useState } from 'react'
import { get, getKey, setKey } from './api.js'
import { Icon } from './ui.jsx'
import Overview from './Overview.jsx'
import LiveView from './LiveView.jsx'
import CallsView from './CallsView.jsx'
import AnalyzeView from './AnalyzeView.jsx'
import PeopleView from './PeopleView.jsx'
import ReviewView from './ReviewView.jsx'
import OpsView from './OpsView.jsx'

const PAGES = [
  ['overview', 'Overview'], ['live', 'Live call'], ['calls', 'Calls'], ['analyze', 'Analyse a transcript'],
  ['people', 'Agents & teams'], ['review', 'Review queue'], ['ops', 'Monitoring'],
]

// Theme: 'system' follows the OS (no data-theme attribute); light/dark set data-theme on <html>.
// The choice is remembered in localStorage when storage is available (it may be blocked).
const THEMES = [['light', 'Light'], ['dark', 'Dark'], ['system', 'System']]
function readTheme() {
  try { const t = localStorage.getItem('theme'); return t === 'light' || t === 'dark' ? t : 'system' } catch { return 'system' }
}
function applyTheme(t) {
  if (t === 'system') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = t
  try { t === 'system' ? localStorage.removeItem('theme') : localStorage.setItem('theme', t) } catch { /* storage blocked */ }
}

// Routing lives in the URL hash (#/calls/syn_001), so the browser back button and links work.
function readRoute() {
  const [page, id] = location.hash.replace(/^#\/?/, '').split('/')
  return { page: PAGES.some(([k]) => k === page) ? page : 'overview', id: id ? decodeURIComponent(id) : null }
}

export default function App() {
  const [route, setRoute] = useState(readRoute())
  const [key, setKeyState] = useState(getKey())
  const [health, setHealth] = useState(null)
  const [pending, setPending] = useState(null)
  const [theme, setTheme] = useState(readTheme())
  const main = useRef(null)

  useEffect(() => {
    const onHash = () => { setRoute(readRoute()); main.current?.scrollTo(0, 0); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  useEffect(() => {
    get('/health').then(setHealth).catch(() => setHealth({ status: 'down' }))
    get('/review?status=pending&limit=500').then((r) => setPending(r.length)).catch(() => {})
  }, [route.page, key])
  useEffect(() => applyTheme(theme), [theme])
  // On phones the nav is a scrolling row: keep the current page's tab visible.
  useEffect(() => { document.querySelector('.nav-item.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }) }, [route.page])

  const go = (page, id) => { location.hash = `/${page}${id ? '/' + encodeURIComponent(id) : ''}` }
  const openCall = (id) => go('calls', id)

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Icon name="logo" /></div>
          <div><div className="brand-name">convo-intel</div><div className="brand-sub">Conversation intelligence</div></div>
        </div>
        <nav className="nav" aria-label="Pages">
          {PAGES.map(([k, label]) => (
            <button key={k} className={`nav-item ${route.page === k ? 'active' : ''}`} onClick={() => go(k)}
              aria-current={route.page === k ? 'page' : undefined}>
              <Icon name={k} />{label}
              {k === 'review' && pending > 0 && <span className="nav-count">{pending}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-theme">
          <div className="seg" role="radiogroup" aria-label="Colour theme">
            {THEMES.map(([k, label]) => (
              <button key={k} role="radio" aria-checked={theme === k} className={theme === k ? 'on' : ''} onClick={() => setTheme(k)}>{label}</button>
            ))}
          </div>
        </div>
        <div className="sidebar-foot">
          <span className={`health-dot ${health?.status === 'ok' ? 'ok' : health ? 'bad' : ''}`}>
            {health?.status === 'ok' ? 'Service healthy' : health ? 'Service degraded' : 'Checking…'}
          </span>
          {health?.config_version && <span>config <code>{health.config_version}</code></span>}
          <input type="password" placeholder="API key (if enabled)" value={key}
            onChange={(e) => { setKeyState(e.target.value); setKey(e.target.value) }} />
        </div>
      </aside>
      <main className="main" ref={main}><div className="main-inner">
        {route.page === 'overview' && <Overview onOpenCall={openCall} go={go} />}
        {route.page === 'live' && <LiveView onOpenCall={openCall} />}
        {route.page === 'calls' && <CallsView selected={route.id} onSelect={openCall} />}
        {route.page === 'analyze' && <AnalyzeView onOpenCall={openCall} />}
        {route.page === 'people' && <PeopleView selected={route.id} onSelect={(id) => go('people', id)} onOpenCall={openCall} />}
        {route.page === 'review' && <ReviewView onOpenCall={openCall} onChange={setPending} />}
        {route.page === 'ops' && <OpsView />}
      </div></main>
    </div>
  )
}
