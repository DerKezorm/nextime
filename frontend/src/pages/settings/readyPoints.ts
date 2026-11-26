/**
 * nextime's own point of "Bereit fürs Internet?" (plan "Einstellungen", E34): "Server erreicht das Internet". The
 * server sends the keys (`backend/app/routers/server_signin.py`, `internet_point`), konten's card reads them in
 * nextime's words: reached, not reached, and no check at all (`NEXTIME_INTERNET_PROBE_URL` empty).
 */
export const INTERNET_POINT = {
  title: 'server.ready.internet.title',
  ok: 'server.ready.internet.ok',
  bad: 'server.ready.internet.bad',
  off: 'server.ready.internet.off',
} as const
