/**
 * Spotify: Authorization Code + PKCE, then Web API playback controls.
 *
 * Redirect URI = this page’s URL with no query (see getRedirectUri()).
 * - Local: http://127.0.0.1:8080/  (add in Spotify Dashboard)
 * - Railway: https://YOUR-SERVICE.up.railway.app/  (add the exact HTTPS URL)
 *
 * Requirements:
 * - Spotify Premium for /me/player/* control
 * - An active Spotify device (open the Spotify app / web player somewhere)
 */
import { SPOTIFY_CLIENT_ID } from './config.js'

const LS = {
  access: 'manifestation_spotify_access',
  refresh: 'manifestation_spotify_refresh',
  expires: 'manifestation_spotify_expires_at',
}
const SS_VERIFIER = 'spotify_pkce_verifier'

const SCOPES = [
  'user-read-playback-state',
  'user-modify-playback-state',
].join(' ')

export function getRedirectUri() {
  const u = new URL(window.location.href)
  u.hash = ''
  u.search = ''
  return u.href
}

function base64url(buf) {
  const s = btoa(String.fromCharCode(...new Uint8Array(buf)))
  return s.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function randomVerifier() {
  const a = new Uint8Array(32)
  crypto.getRandomValues(a)
  return base64url(a)
}

async function challengeFromVerifier(verifier) {
  const data = new TextEncoder().encode(verifier)
  const digest = await crypto.subtle.digest('SHA-256', data)
  return base64url(digest)
}

export async function beginSpotifyLogin() {
  if (!SPOTIFY_CLIENT_ID?.trim()) {
    throw new Error('Set SPOTIFY_CLIENT_ID in src/config.js')
  }
  const verifier = randomVerifier()
  sessionStorage.setItem(SS_VERIFIER, verifier)
  const challenge = await challengeFromVerifier(verifier)
  const redirect = getRedirectUri()
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: SPOTIFY_CLIENT_ID.trim(),
    scope: SCOPES,
    redirect_uri: redirect,
    code_challenge_method: 'S256',
    code_challenge: challenge,
  })
  window.location.assign(
    `https://accounts.spotify.com/authorize?${params.toString()}`,
  )
}

async function exchangeCode(code, verifier) {
  const redirect = getRedirectUri()
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    redirect_uri: redirect,
    client_id: SPOTIFY_CLIENT_ID.trim(),
    code_verifier: verifier,
  })
  const res = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.error_description || data.error || 'Token exchange failed')
  }
  localStorage.setItem(LS.access, data.access_token)
  localStorage.setItem(LS.refresh, data.refresh_token)
  localStorage.setItem(LS.expires, String(Date.now() + data.expires_in * 1000))
}

async function refreshTokens() {
  const refresh = localStorage.getItem(LS.refresh)
  if (!refresh) throw new Error('Not connected — use Connect Spotify')
  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    refresh_token: refresh,
    client_id: SPOTIFY_CLIENT_ID.trim(),
  })
  const res = await fetch('https://accounts.spotify.com/api/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    logout()
    throw new Error(data.error_description || 'Session expired — connect again')
  }
  localStorage.setItem(LS.access, data.access_token)
  if (data.refresh_token) localStorage.setItem(LS.refresh, data.refresh_token)
  localStorage.setItem(LS.expires, String(Date.now() + data.expires_in * 1000))
}

export function logout() {
  localStorage.removeItem(LS.access)
  localStorage.removeItem(LS.refresh)
  localStorage.removeItem(LS.expires)
}

async function getAccessToken() {
  if (!SPOTIFY_CLIENT_ID?.trim()) throw new Error('Set SPOTIFY_CLIENT_ID in src/config.js')
  const exp = Number(localStorage.getItem(LS.expires) || 0)
  if (Date.now() < exp - 60_000) {
    return localStorage.getItem(LS.access)
  }
  await refreshTokens()
  return localStorage.getItem(LS.access)
}

async function api(method, path, body, didRefresh = false) {
  const token = await getAccessToken()
  const res = await fetch(`https://api.spotify.com/v1${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && !didRefresh) {
    await refreshTokens()
    return api(method, path, body, true)
  }
  if (res.ok || res.status === 204) return res
  let msg = `${res.status}`
  try {
    const err = await res.json()
    if (err.error?.message) msg = err.error.message
    else if (err.message) msg = err.message
  } catch {
    /* ignore */
  }
  if (res.status === 404) {
    throw new Error('No active Spotify device — open Spotify on phone or desktop')
  }
  if (res.status === 403) {
    throw new Error('Spotify Premium required for playback control')
  }
  throw new Error(msg)
}

export function isSpotifyConnected() {
  return Boolean(localStorage.getItem(LS.refresh))
}

/** Call on load: completes OAuth redirect if ?code= is present. */
export async function initSpotifyAuth() {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('code')
  const err = params.get('error')
  const cleanUrl = getRedirectUri()

  if (err) {
    history.replaceState({}, '', cleanUrl)
    throw new Error(params.get('error_description') || err)
  }
  if (!code) return null

  const verifier = sessionStorage.getItem(SS_VERIFIER)
  if (!verifier) {
    history.replaceState({}, '', cleanUrl)
    return null
  }

  try {
    await exchangeCode(code, verifier)
  } catch (e) {
    sessionStorage.removeItem(SS_VERIFIER)
    history.replaceState({}, '', cleanUrl)
    throw e
  }
  sessionStorage.removeItem(SS_VERIFIER)
  history.replaceState({}, '', cleanUrl)
  return 'Spotify connected'
}

const LABELS = {
  play: 'Play',
  pause: 'Pause',
  next: 'Next',
  previous: 'Previous',
}

export async function spotifyPlayback(cmd) {
  if (!isSpotifyConnected()) {
    throw new Error('Connect Spotify first (button below)')
  }
  switch (cmd) {
    case 'play':
      await api('PUT', '/me/player/play', {})
      return LABELS.play
    case 'pause':
      await api('PUT', '/me/player/pause')
      return LABELS.pause
    case 'next':
      await api('POST', '/me/player/next')
      return LABELS.next
    case 'previous':
      await api('POST', '/me/player/previous')
      return LABELS.previous
    default:
      throw new Error(`Unknown command: ${cmd}`)
  }
}
