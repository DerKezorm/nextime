/** Made-up connected sources for the tests of TIME-15 to TIME-18 (no page imports this). */
import type { Choices, GoogleSetup, Preview, SourceRow, Sources } from './features/sources/api'

export function row(id: number, change: Partial<SourceRow> = {}): SourceRow {
  return {
    id,
    kind: 'icloud',
    label: 'familie@example.com',
    calendars: [
      { id: 11, name: 'Arbeit', color: 'teal', read_only: false },
      { id: 12, name: 'Persönlich', color: 'orange', read_only: false },
    ],
    state: 'ok',
    since: null,
    synced_at: '2026-11-26T15:04:00Z',
    problem: '',
    waiting: 0,
    unreadable: 0,
    new: 0,
    loading: false,
    events: 120,
    ...change,
  }
}

export function sources(rows: SourceRow[], change: Partial<Sources> = {}): Sources {
  return { sources: rows, notices: [], events_total: 400, events_smooth_up_to: 20000, google: { ready: false, configured: false }, ...change }
}

export function choices(change: Partial<Choices> = {}): Choices {
  return {
    source_id: 1,
    kind: 'icloud',
    label: 'familie@example.com',
    calendars: [
      { url: '/arbeit/', name: 'Arbeit', color: 'teal', read_only: false, new: false, chosen: true },
      { url: '/persoenlich/', name: 'Persönlich', color: 'orange', read_only: false, new: false, chosen: true },
      { url: '/team/', name: 'Team (von Chris)', color: 'violet', read_only: true, new: false, chosen: false },
      { url: '/garten/', name: 'Garten', color: 'green', read_only: false, new: true, chosen: false },
    ],
    ...change,
  }
}

export function preview(change: Partial<Preview> = {}): Preview {
  return { name: 'Abfallkalender Musterstadt', count: 74, first: '2027-01-04', last: '2027-12-28', over: false, events_total: 400, events_smooth_up_to: 20000, exists: null, ...change }
}

export function googleSetup(change: Partial<GoogleSetup> = {}): GoogleSetup {
  return {
    client_id: '',
    secret_set: false,
    redirect_uri: 'https://nextime.example.com/api/google/callback',
    https: true,
    ready: false,
    enabled: false,
    members: 0,
    redirect_changed: false,
    ...change,
  }
}

export const COUNTRIES = {
  countries: [
    {
      code: 'DE',
      name: 'Deutschland',
      school: true,
      regions: [
        ['BW', 'Baden-Württemberg'], ['BY', 'Bayern'], ['BE', 'Berlin'], ['BB', 'Brandenburg'], ['HB', 'Bremen'], ['HH', 'Hamburg'],
        ['HE', 'Hessen'], ['MV', 'Mecklenburg-Vorpommern'], ['NI', 'Niedersachsen'], ['NW', 'Nordrhein-Westfalen'], ['RP', 'Rheinland-Pfalz'],
        ['SL', 'Saarland'], ['SN', 'Sachsen'], ['ST', 'Sachsen-Anhalt'], ['SH', 'Schleswig-Holstein'], ['TH', 'Thüringen'],
      ].map(([code, name]) => ({ code, name })),
    },
    { code: 'AT', name: 'Österreich', school: true, regions: [{ code: '9', name: 'Wien' }, { code: '7', name: 'Tirol' }] },
    { code: 'CH', name: 'Schweiz', school: false, regions: [{ code: 'ZH', name: 'Zürich' }, { code: 'BE', name: 'Bern' }] },
  ],
}
