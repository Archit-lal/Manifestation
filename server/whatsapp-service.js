import twilio from 'twilio'
import { buildMessageBody, getIntentDefinition } from './intent-map.js'

const SANDBOX_FROM = 'whatsapp:+14155238886'

function cooldownMs() {
  const n = Number(process.env.ALERT_COOLDOWN_MS ?? process.env.SMS_COOLDOWN_MS)
  if (Number.isFinite(n) && n >= 0) return n
  return 60_000
}

function env() {
  return {
    accountSid: process.env.TWILIO_ACCOUNT_SID,
    authToken: process.env.TWILIO_AUTH_TOKEN,
    caregiver:
      process.env.CAREGIVER_PHONE_NUMBER?.trim() ||
      process.env.CAREGIVER_SMS_TO?.trim() ||
      process.env.TWILIO_TO?.trim() ||
      null,
  }
}

function sandboxOn() {
  return (
    process.env.USE_TWILIO_WHATSAPP_SANDBOX === '1' ||
    process.env.TWILIO_WHATSAPP_SANDBOX === '1'
  )
}

function toWa(addr) {
  const t = addr.trim()
  if (t.toLowerCase().startsWith('whatsapp:')) return t
  const d = t.replace(/\D/g, '')
  if (!d) return 'whatsapp:'
  return `whatsapp:+${d}`
}

let cached = null

function client() {
  const { accountSid, authToken } = env()
  if (!accountSid || !authToken) return null
  if (cached?.accountSid === accountSid && cached?.authToken === authToken) {
    return cached.tw
  }
  const tw = twilio(accountSid, authToken)
  cached = { accountSid, authToken, tw }
  return tw
}

const lastOk = new Map()

function hint(err) {
  const e = err
  const c = e.code
  const m = (e.message ?? String(err)).toLowerCase()
  if (c === 21211) return 'Use E.164 for CAREGIVER_PHONE_NUMBER e.g. +15551234567'
  if (c === 20003 || m.includes('authenticate'))
    return 'Check TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN'
  if (e.status === 401 || e.status === 403) return 'Twilio auth failed'
  if (c === 63015)
    return 'Join Twilio WhatsApp sandbox on this phone first (+1 415 523 8886)'
  return e.message ?? String(err)
}

function last4(phone) {
  const d = phone.replace(/\D/g, '')
  return d.length < 4 ? '????' : d.slice(-4)
}

async function sendOnce(to, from, body) {
  const tw = client()
  if (!tw) throw new Error('no_client')
  const msg = await tw.messages.create({ to, from, body })
  if (!msg.sid) throw new Error('no_sid')
  return { sid: msg.sid, twilioStatus: typeof msg.status === 'string' ? msg.status : undefined }
}

async function sendRetry(to, from, body) {
  try {
    return await sendOnce(to, from, body)
  } catch (e1) {
    return await sendOnce(to, from, body)
  }
}

async function send(body) {
  if (!sandboxOn()) {
    return { ok: false, error: 'not_configured', hint: 'Set USE_TWILIO_WHATSAPP_SANDBOX=1' }
  }
  const { accountSid, authToken, caregiver } = env()
  if (!caregiver || !accountSid || !authToken) {
    return {
      ok: false,
      error: 'not_configured',
      hint: 'Set Twilio vars and CAREGIVER_PHONE_NUMBER',
    }
  }
  const to = toWa(caregiver)
  const lf = last4(caregiver)
  try {
    const r = await sendRetry(to, SANDBOX_FROM, body)
    return { ok: true, ...r, toLastFour: lf }
  } catch (e) {
    return { ok: false, error: String(e), hint: hint(e) }
  }
}

export async function triggerAlert(intent) {
  const key = intent.trim().toLowerCase()
  const def = getIntentDefinition(key)
  if (!def) {
    return {
      success: false,
      intent: key,
      message: '',
      skipped: 'unknown_intent',
      error: `Unknown intent ${key}`,
    }
  }

  const message = buildMessageBody(key) ?? ''
  if (!message) {
    return { success: false, intent: key, message: '', error: 'empty_message' }
  }

  if (!sandboxOn() || !env().accountSid || !env().authToken || !env().caregiver) {
    return {
      success: false,
      intent: key,
      message,
      skipped: 'not_configured',
      error: 'Not configured',
      hint: 'Copy .env.example to .env and fill Twilio fields',
    }
  }

  const now = Date.now()
  const prev = lastOk.get(key) ?? 0
  if (now - prev < cooldownMs()) {
    return {
      success: false,
      intent: key,
      message,
      skipped: 'cooldown',
      error: 'Cooldown',
      hint: `Wait ${Math.ceil(cooldownMs() / 1000)}s (ALERT_COOLDOWN_MS)`,
    }
  }

  const result = await send(message)
  if (result.ok) {
    lastOk.set(key, Date.now())
    return {
      success: true,
      intent: key,
      message,
      sid: result.sid,
      twilioStatus: result.twilioStatus,
      toLastFour: result.toLastFour,
    }
  }
  return {
    success: false,
    intent: key,
    message,
    error: result.error ?? 'send_failed',
    hint: result.hint,
  }
}
