/**
 * WhatsApp alerts via same-origin POST /api/alert (see server/).
 * Called from app.js the same way as spotifyPlayback for music tiles.
 */

const LABELS = {
  help: 'Help',
  hungry: 'Hungry',
  thirsty: 'Thirsty',
  sick: 'Sick',
}

export async function messageAlert(intent) {
  const key = intent.trim().toLowerCase()
  const res = await fetch('/api/alert', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ intent: key }),
  })
  const data = await res.json().catch(() => ({}))
  if (!data.success) {
    throw new Error(data.hint || data.error || 'Send failed')
  }
  const tail = data.toLastFour ? ` …${data.toLastFour}` : ''
  const name = LABELS[key] || key
  return `${name} · WhatsApp sent${tail}`
}
