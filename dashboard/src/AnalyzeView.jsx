import { useState } from 'react'
import { post } from './api.js'
import { Card, PageHead } from './ui.jsx'

const SAMPLE = `Agent: Thank you for calling Nimbus Telecom, this is Ravi. How can I help you today?
Customer: My bill is 400 rupees too high this month and I am honestly fed up. My account number is 55821934.
Agent: I'm sorry about that, I understand how frustrating a wrong bill is. I can see a roaming pack was added by mistake.
Agent: I guarantee you a full refund today.
Customer: Fine, but if this happens again I am switching to another provider.
Agent: I will raise the refund request now and you will get an SMS confirmation. Is there anything else I can help with?
Customer: No, that's all.
Agent: Thank you for your patience. Have a good day.`

// Paste a transcript or chat log, analyse it synchronously, then open the result.
export default function AnalyzeView({ onOpenCall }) {
  const [text, setText] = useState(SAMPLE)
  const [agent, setAgent] = useState('agent_01')
  const [team, setTeam] = useState('team_north')
  const [channel, setChannel] = useState('voice')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit() {
    setBusy(true); setError(null)
    try {
      const r = await post('/calls', { transcript: text, agent_id: agent, team_id: team, channel, analyze: 'sync', demo: true })
      onOpenCall(r.call_id)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHead title="Analyse a transcript">
        Paste a call transcript or chat log, one turn per line as <code>Agent: …</code> / <code>Customer: …</code>.
        Personal data is redacted before anything reaches the LLM.
      </PageHead>
      <Card>
        <div className="stack">
          <div className="form-grid">
            <label>Agent<input value={agent} onChange={(e) => setAgent(e.target.value)} /></label>
            <label>Team<input value={team} onChange={(e) => setTeam(e.target.value)} /></label>
            <label>Channel
              <select value={channel} onChange={(e) => setChannel(e.target.value)}><option value="voice">voice</option><option value="chat">chat</option></select>
            </label>
          </div>
          <label className="field">Transcript<textarea rows={14} value={text} onChange={(e) => setText(e.target.value)} /></label>
          <div className="row">
            <button className="btn primary" onClick={submit} disabled={busy || !text.trim()}>
              {busy ? <><span className="spinner" /> Analysing… (≈5–10 s)</> : 'Analyse'}
            </button>
            <button className="btn" onClick={() => setText(SAMPLE)} disabled={busy}>Reset sample</button>
            <span className="muted small">Needs <code>GEMINI_API_KEY</code> on the server.</span>
          </div>
          {error && <div className="notice">Analysis failed: {error}</div>}
        </div>
      </Card>
    </>
  )
}
