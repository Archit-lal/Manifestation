/**
 * Blink → next tile, clench → activate.
 * Music → spotifyPlayback. Messages → messageAlert (same pattern).
 */
import {
  initSpotifyAuth,
  beginSpotifyLogin,
  spotifyPlayback,
} from './spotify.js'
import { messageAlert } from './messages.js'

const MENUS = {
  home: [
    { label: 'Music', action: 'goto:music' },
    { label: 'Messages', action: 'goto:messages' },
  ],
  music: [
    { label: 'Play', action: 'music:play' },
    { label: 'Pause', action: 'music:pause' },
    { label: 'Next', action: 'music:next' },
    { label: 'Previous', action: 'music:previous' },
    { label: '← Back', action: 'goto:home' },
  ],
  messages: [
    { label: 'Help', action: 'message:help' },
    { label: 'Hungry', action: 'message:hungry' },
    { label: 'Thirsty', action: 'message:thirsty' },
    { label: 'Sick', action: 'message:sick' },
    { label: '← Back', action: 'goto:home' },
  ],
}

let state = {
  menu: 'home',
  index: 0,
  flash: null,
}

let clearFlashTimer = null
let prevRenderKey = null

function currentItems() {
  return MENUS[state.menu].map((e) => e.label)
}

function setFlash(text) {
  state.flash = text
  if (clearFlashTimer) clearTimeout(clearFlashTimer)
  clearFlashTimer = setTimeout(() => {
    state.flash = null
    clearFlashTimer = null
    render()
  }, 1800)
}

function applyAction(action) {
  if (action.startsWith('goto:')) {
    state.menu = action.slice(5)
    state.index = 0
    state.flash = null
    if (clearFlashTimer) {
      clearTimeout(clearFlashTimer)
      clearFlashTimer = null
    }
    return
  }
  if (action.startsWith('message:')) {
    const key = action.slice(8)
    void messageAlert(key)
      .then((msg) => setFlash(msg))
      .catch((e) => setFlash(e.message || 'Message'))
    return
  }
  if (action.startsWith('music:')) {
    const key = action.slice(6)
    void spotifyPlayback(key)
      .then((msg) => setFlash(msg))
      .catch((e) => setFlash(e.message || 'Spotify'))
    return
  }
}

function signal(name) {
  const row = MENUS[state.menu]
  if (!row?.length) return

  if (name === 'blink') {
    state.index = (state.index + 1) % row.length
  } else if (name === 'clench') {
    applyAction(row[state.index].action)
  }
  render()
}

function render() {
  const tilesEl = document.getElementById('tiles')
  const menuNameEl = document.getElementById('menu-name')
  const flashEl = document.getElementById('flash')
  if (!tilesEl || !menuNameEl || !flashEl) return

  const items = currentItems()
  const view = {
    menu: state.menu,
    items,
    index: state.index,
    flash: state.flash,
  }

  menuNameEl.textContent =
    view.menu.charAt(0).toUpperCase() + view.menu.slice(1)

  const itemsKey = view.items.join('|') + ':' + view.index
  if (itemsKey !== prevRenderKey) {
    tilesEl.innerHTML = ''
    view.items.forEach((label, i) => {
      const div = document.createElement('div')
      div.className =
        'tile' +
        (i === view.index ? ' active' : '') +
        (label.startsWith('←') ? ' back' : '')
      div.textContent = label
      div.onclick = () => signal('clench')
      tilesEl.appendChild(div)
    })
  } else {
    tilesEl.querySelectorAll('.tile').forEach((t, i) => {
      t.classList.toggle('active', i === view.index)
    })
  }

  if (view.flash) {
    flashEl.textContent = view.flash
    flashEl.classList.add('visible')
  } else {
    flashEl.classList.remove('visible')
  }

  prevRenderKey = itemsKey
}

function connectSpotify() {
  beginSpotifyLogin().catch((e) => setFlash(e.message || 'Spotify'))
}

async function bootstrap() {
  try {
    const msg = await initSpotifyAuth()
    if (msg) setFlash(msg)
  } catch (e) {
    setFlash(e.message || 'Spotify auth error')
  }
  render()
}

function init() {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'b' || e.key === 'B') signal('blink')
    if (e.key === 'j' || e.key === 'J') signal('clench')
  })

  void bootstrap()
}

window.signal = signal
window.connectSpotify = connectSpotify
init()
