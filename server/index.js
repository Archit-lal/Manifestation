import './load-env.js'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import express from 'express'
import { triggerAlert } from './whatsapp-service.js'

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
const port = Number(process.env.PORT) || 3000
const host = process.env.HOST || '0.0.0.0'

const app = express()
app.disable('x-powered-by')
app.use(express.json({ limit: '16kb' }))

app.get('/health', (_req, res) => {
  res.json({ ok: true })
})

app.post('/api/alert', async (req, res) => {
  const raw = req.body?.intent
  const intent = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
  if (!intent) {
    res.status(400).json({ success: false, error: 'missing_intent' })
    return
  }
  const out = await triggerAlert(intent)
  if (!out.success && out.skipped === 'unknown_intent') {
    res.status(400).json(out)
    return
  }
  res.json(out)
})

// API routes above; static last
app.use(express.static(root))

http
  .createServer(app)
  .on('error', (err) => {
    console.error(err)
    process.exit(1)
  })
  .listen(port, host, () => {
    console.log(`listening ${host}:${port}`)
  })
