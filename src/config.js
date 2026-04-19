/**
 * 1) https://developer.spotify.com/dashboard → Create app
 * 2) Redirect URIs: add this page’s URL exactly (e.g. https://YOUR-APP.up.railway.app/)
 *    plus optional http://127.0.0.1:3000/ for local dev — then Save.
 * 3) Enable “Web API” on the app.
 * 4) Paste Client ID below (OK in browser with PKCE; never put Client Secret in frontend).
 */
export const SPOTIFY_CLIENT_ID = '62e83b8053fd46809504ac979d45d728'
