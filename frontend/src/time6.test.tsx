/** TIME-6 in the interface: the sentence of a rule in German and English, the dialog "Wiederholung", its checks, the
 * fifth weekday, the question of a series, and a rule from elsewhere. */
import App from './App'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import type { EventDetail, Rule } from './features/calendars/api'
import { RecurrenceDialog } from './features/calendars/Recurrence'
import { rruleSentence, ruleSentence } from './features/calendars/rule'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, choose, click, me, ok, render, serve, signedIn, start, stop, type } from './testing'

const anna = me({ display: { week_start: 1, week_numbers: true, clock: '24', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-26T15:05:00Z'))
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

const rule = (change: Partial<Rule>): Rule => ({ freq: 'weekly', interval: 1, weekdays: ['TH'], monthly: 'date', end: 'never', until: null, count: null, ...change })
const evening = { allDay: false, start: '18:30', end: '20:00' }

describe('TIME-6 the sentence', () => {
  it('TIME-6 AC-4 AC-1 says rhythm, days, time and end for every kind of rule in German', () => {
    const t = i18n.t.bind(i18n)
    expect(ruleSentence(rule({ weekdays: ['MO', 'TH'] }), '2026-11-26', evening, t, 'de')).toBe('Jeden Montag und Donnerstag, 18:30–20:00')
    expect(ruleSentence(rule({ freq: 'daily', interval: 2 }), '2026-11-26', evening, t, 'de')).toBe('Alle 2 Tage, 18:30–20:00')
    expect(ruleSentence(rule({ interval: 3, weekdays: ['MO', 'WE', 'FR'] }), '2026-11-26', null, t, 'de')).toBe('Alle 3 Wochen am Montag, Mittwoch und Freitag')
    expect(ruleSentence(rule({ freq: 'monthly' }), '2026-11-26', null, t, 'de')).toBe('Jeden Monat am 26.')
    expect(ruleSentence(rule({ freq: 'monthly', monthly: 'nth' }), '2026-11-26', null, t, 'de')).toBe('Jeden Monat am 4. Donnerstag')
    expect(ruleSentence(rule({ freq: 'monthly', monthly: 'last', interval: 2 }), '2026-11-26', null, t, 'de')).toBe('Alle 2 Monate am letzten Donnerstag')
    expect(ruleSentence(rule({ freq: 'yearly' }), '2026-11-26', { allDay: true, start: '', end: '' }, t, 'de')).toBe('Jedes Jahr am 26. November, ganztägig')
    expect(ruleSentence(rule({ end: 'until', until: '2027-11-26' }), '2026-11-26', null, t, 'de')).toBe('Jeden Donnerstag, bis 26.11.2027')
    expect(ruleSentence(rule({ end: 'count', count: 10 }), '2026-11-26', null, t, 'de')).toBe('Jeden Donnerstag, 10 Mal')
  })

  it('TIME-6 AC-4 says the same in English', async () => {
    await i18n.changeLanguage('en')
    const t = i18n.t.bind(i18n)
    expect(ruleSentence(rule({ weekdays: ['MO', 'TH'] }), '2026-11-26', evening, t, 'en', true)).toBe('Every Monday and Thursday, 6:30 PM–8:00 PM')
    expect(ruleSentence(rule({ freq: 'monthly' }), '2026-11-22', null, t, 'en')).toBe('Every month on the 22nd')
    expect(ruleSentence(rule({ freq: 'monthly', monthly: 'nth' }), '2026-11-10', null, t, 'en')).toBe('Every month on the second Tuesday')
    expect(ruleSentence(rule({ freq: 'yearly', interval: 2, end: 'count', count: 1 }), '2026-11-26', null, t, 'en')).toBe('Every 2 years on November 26, 1 time')
    expect(ruleSentence(rule({ freq: 'daily', end: 'until', until: '2027-01-31' }), '2026-11-26', null, t, 'en')).toBe('Every day, until 1/31/2027')
  })

  it('TIME-6 EC-9 a rule nextime does not offer is said as a sentence', () => {
    const t = i18n.t.bind(i18n)
    expect(rruleSentence('FREQ=MONTHLY;BYDAY=2TU,4TU', '2026-11-10', t, 'de')).toBe('Jeden Monat am 2. Dienstag und 4. Dienstag')
    expect(rruleSentence('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;COUNT=5', '2026-11-23', t, 'de')).toBe('Alle 2 Wochen am Montag, 5 Mal')
  })
})

describe('TIME-6 the dialog', () => {
  function dialog(first: string, start = rule({})) {
    const applied: (Rule | null)[] = []
    return {
      applied,
      node: <RecurrenceDialog title="Training" first={first} times={evening} rule={start} twelve={false} onApply={(value) => applied.push(value)} onClose={() => undefined} />,
    }
  }
  const here = () => document.querySelector<HTMLElement>('[data-testid="rule-dialog"]')!

  it('TIME-6 AC-5 AC-1 offers every n, the weekdays, the months, the end and a sentence that follows', async () => {
    const { applied, node } = dialog('2026-11-26')
    await render(node)
    expect(document.querySelector('[role="dialog"]:not([data-event-dialog])')?.getAttribute('aria-label')).toBe('Wiederholung · Training')
    expect([...here().querySelectorAll('[data-testid="rule-freq"] option')].map((option) => option.textContent)).toEqual(['Tage', 'Wochen', 'Monate', 'Jahre'])
    expect([...here().querySelectorAll('[data-testid^="rule-day-"]')].map((button) => button.textContent)).toEqual(['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'])
    expect(here().querySelector('[data-testid="rule-day-TH"]')?.getAttribute('aria-pressed')).toBe('true')
    const sentence = () => here().querySelector('[data-testid="rule-sentence"]')!.textContent
    expect(sentence()).toBe('Jeden Donnerstag, 18:30–20:00')
    await click(here().querySelector('[data-testid="rule-day-MO"]'))
    expect(sentence()).toBe('Jeden Montag und Donnerstag, 18:30–20:00')
    type(here().querySelector<HTMLInputElement>('[data-testid="rule-interval"]')!, '2')
    expect(sentence()).toBe('Alle 2 Wochen am Montag und Donnerstag, 18:30–20:00')
    await click(here().querySelector('[data-testid="rule-end-count"]'))
    type(here().querySelector<HTMLInputElement>('[data-testid="rule-count"]')!, '6')
    expect(sentence()).toBe('Alle 2 Wochen am Montag und Donnerstag, 18:30–20:00, 6 Mal')
    expect(here().querySelector('[data-testid="rule-sentence"]')?.className).toContain('break-words')
    await choose(here().querySelector<HTMLSelectElement>('[data-testid="rule-freq"]')!, 'monthly')
    expect([...here().querySelectorAll('fieldset')[0].querySelectorAll('label')].map((label) => label.textContent)).toEqual(['am 26.', 'am 4. Donnerstag', 'am letzten Donnerstag'])
    await choose(here().querySelector<HTMLSelectElement>('[data-testid="rule-freq"]')!, 'weekly')
    await click(byText('Übernehmen', here()))
    expect(applied).toEqual([{ freq: 'weekly', interval: 2, weekdays: ['TH', 'MO'], monthly: 'date', end: 'count', until: null, count: 6 }])
  })

  it('TIME-6 EC-8 invalid input stands at its field and nothing is taken', async () => {
    const { applied, node } = dialog('2026-11-26')
    await render(node)
    type(here().querySelector<HTMLInputElement>('[data-testid="rule-interval"]')!, '0')
    await click(here().querySelector('[data-testid="rule-day-TH"]'))
    await click(byText('Übernehmen', here()))
    expect(here().textContent).toContain('Bitte eine ganze Zahl ab 1.')
    expect(here().textContent).toContain('Bitte mindestens einen Wochentag wählen.')
    type(here().querySelector<HTMLInputElement>('[data-testid="rule-interval"]')!, '1')
    await click(here().querySelector('[data-testid="rule-day-TH"]'))
    await click(here().querySelector('[data-testid="rule-end-until"]'))
    type(here().querySelector<HTMLInputElement>('[data-testid="rule-until"]')!, '2026-11-01')
    await click(byText('Übernehmen', here()))
    expect(here().textContent).toContain('Das Enddatum liegt vor dem Beginn.')
    await click(here().querySelector('[data-testid="rule-end-count"]'))
    type(here().querySelector<HTMLInputElement>('[data-testid="rule-count"]')!, '0')
    await click(byText('Übernehmen', here()))
    expect(here().textContent).toContain('Bitte mindestens 1 Termin.')
    expect(applied).toEqual([])
  })

  it('TIME-6 EC-4 a start on the fifth Friday offers "the last Friday" instead', async () => {
    const { node } = dialog('2027-01-29', rule({ freq: 'monthly' }))
    await render(node)
    const options = [...here().querySelectorAll('fieldset')[0].querySelectorAll('label')].map((label) => label.textContent)
    expect(options).toEqual(['am 29.', 'am letzten Freitag'])
    expect(here().querySelector('[data-testid="rule-nth"]')).toBeNull()
    // A rule "on the 5th Friday" from before reads as the last Friday too.
    expect(ruleSentence(rule({ freq: 'monthly', monthly: 'nth' }), '2027-01-29', null, i18n.t.bind(i18n), 'de')).toBe('Jeden Monat am letzten Freitag')
  })
})

describe('TIME-6 changing a series', () => {
  const series = (change: Partial<EventDetail> = {}) =>
    eventDetail({ recurring: true, occurrence: '20261126T173000Z', rule: rule({ weekdays: ['TH'] }), rrule: 'FREQ=WEEKLY;BYDAY=TH', ...change })

  function server(detail: EventDetail) {
    const lists = calendarList([calendar(1, 'Persönlich', { default: true })])
    serve(
      signedIn(anna, (method, path) => {
        if (path.startsWith('/api/calendars')) return ok(lists)
        if (path.startsWith('/api/events?'))
          return ok([{ id: 7, occurrence: detail.occurrence, calendar_id: 1, title: detail.title, busy: true, busy_only: false, recurring: true, all_day: false, start: '2026-11-26T17:30:00Z', end: '2026-11-26T19:00:00Z', time_zone: 'Europe/Berlin' }])
        if (path.startsWith('/api/events/7') && method === 'GET') return ok(detail)
        if (path.startsWith('/api/events/7')) return method === 'DELETE' ? { status: 204, body: null } : ok(detail)
        return undefined
      }),
    )
  }

  async function openSeries(detail: EventDetail): Promise<void> {
    server(detail)
    await render(
      <AuthProvider>
        <App />
      </AuthProvider>,
      '/',
    )
    await click(box.querySelector('[data-testid="next"], [data-testid="today-row"], [data-testid="next-day-row"]'))
  }

  it('TIME-6 AC-2 moving an occurrence asks with the change in the heading and the day in the sentence', async () => {
    await openSeries(series())
    type(document.querySelector<HTMLInputElement>('[data-testid="field-start-time"]')!, '19:00')
    type(document.querySelector<HTMLInputElement>('[data-testid="field-end-time"]')!, '20:30')
    await click(document.querySelector('[data-testid="panel-save"]'))
    const question = document.querySelector<HTMLElement>('[data-testid="series-question"]')!
    expect(document.querySelectorAll('[role="dialog"]:not([data-event-dialog])')[0]?.getAttribute('aria-label')).toBe('„Training“ auf 19:00 verschieben?')
    expect(question.textContent).toMatch(/^Der Termin am Do\.?, 26\. November gehört zu einer Serie\./)
    expect([...question.querySelectorAll('button')].map((button) => button.textContent)).toEqual(['Nur diesen Termin', 'Diesen und alle folgenden', 'Alle Termine der Serie', 'Abbrechen'])
    await click(byText('Nur diesen Termin', question))
    expect(calls.find((call) => call.method === 'PATCH')?.body).toMatchObject({ scope: 'this', occurrence: '20261126T173000Z', start: '2026-11-26T19:00', end: '2026-11-26T20:30' })
  })

  it('TIME-6 AC-2 deleting an occurrence asks the same, and "Abbrechen" leaves everything', async () => {
    await openSeries(series())
    await click(document.querySelector('[data-testid="panel-delete"]'))
    expect(document.querySelector('[role="dialog"]:not([data-event-dialog])')?.getAttribute('aria-label')).toBe('„Training“ löschen?')
    await click(byText('Abbrechen', document.querySelector<HTMLElement>('[data-testid="series-question"]')!))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(document.querySelector('[data-testid="panel-delete"]'))
    await click(byText('Alle Termine der Serie'))
    expect(calls.find((call) => call.method === 'DELETE')?.path).toBe('/api/events/7?scope=all&occurrence=20261126T173000Z')
  })

  it('TIME-6 AC-2 a new rule offers only "following" and "all"', async () => {
    await openSeries(series())
    await click(document.querySelector('[data-testid="field-rule"]'))
    await click(document.querySelector('[data-testid="rule-day-MO"]'))
    await click(byText('Übernehmen'))
    await click(document.querySelector('[data-testid="panel-save"]'))
    const question = document.querySelector<HTMLElement>('[data-testid="series-question"]')!
    expect(document.querySelector('[role="dialog"]:not([data-event-dialog])')?.getAttribute('aria-label')).toBe('„Training“ ändern?')
    expect(byText('Nur diesen Termin', question)).toBeUndefined()
    await click(byText('Diesen und alle folgenden', question))
    expect(calls.find((call) => call.method === 'PATCH')?.body).toMatchObject({ scope: 'following', rule: { freq: 'weekly', weekdays: ['TH', 'MO'] } })
  })

  it('TIME-6 EC-9 a rule from elsewhere shows as a sentence and stays when only the title changes', async () => {
    await openSeries(series({ rule: null, rrule: 'FREQ=MONTHLY;BYDAY=2TU,4TU' }))
    expect(document.querySelector('[data-testid="field-rule"]')?.textContent).toBe('Jeden Monat am 2. Dienstag und 4. Dienstag')
    type(document.querySelector<HTMLInputElement>('[data-testid="field-title"]')!, 'Stammtisch')
    await click(document.querySelector('[data-testid="panel-save"]'))
    await click(byText('Alle Termine der Serie'))
    const body = calls.find((call) => call.method === 'PATCH')?.body as Record<string, unknown>
    expect(body).toMatchObject({ title: 'Stammtisch', scope: 'all' })
    expect('rule' in body).toBe(false)
  })
})
