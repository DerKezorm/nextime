/** No sliding switch anywhere (decided while testing, 2026-11-23): every on/off is the native checkbox in the accent,
 * in dialogs as a box with its name beside it and a quiet hint below; the settings rows keep their `Toggle`. */

import { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'

import { CheckLine } from './CheckLine'

type Files = {
  readFileSync: (file: string, encoding: string) => string
  readdirSync: (folder: string, options: { recursive: true }) => string[]
}
const fsName = 'node:fs'
const files = (await import(/* @vite-ignore */ fsName)) as Files

function Holder() {
  const [on, setOn] = useState(false)
  return <CheckLine label="Als Hintergrund zeigen" hint="Feiertage, Geburtstage" checked={on} onChange={setOn} testId="box" />
}

describe('checkboxes instead of switches', () => {
  it('no sliding switch is left in the code or the styles', () => {
    const found = files
      .readdirSync('src', { recursive: true })
      .map((name) => name.replaceAll('\\', '/'))
      .filter((name) => /\.(tsx?|css)$/.test(name) && !name.includes('.test.'))
      .filter((name) => files.readFileSync(`src/${name}`, 'utf-8').includes('nt-switch'))
    expect(found).toEqual([])
  })

  it('a check line is the native checkbox in the accent, the box left, name and quiet hint beside it; the name ticks it', () => {
    const host = document.createElement('div')
    document.body.append(host)
    act(() => createRoot(host).render(<Holder />))
    const box = host.querySelector<HTMLInputElement>('[data-testid="box"]')!
    expect(box.tagName).toBe('INPUT')
    expect(box.type).toBe('checkbox')
    // Read out as the checkbox it is, never as a switch (review of the additions, 2026-11-23).
    expect(box.getAttribute('role')).toBeNull()
    expect(box.className).toContain('accent-[var(--color-accent-500)]')
    const label = host.querySelector('label')!
    expect(label.firstElementChild).toBe(box)
    expect(label.textContent).toBe('Als Hintergrund zeigenFeiertage, Geburtstage')
    expect(host.querySelector('.text-mist-500')?.textContent).toBe('Feiertage, Geburtstage')
    act(() => (host.querySelector('.text-mist-100') as HTMLElement).click())
    expect(box.checked).toBe(true)
    expect(box.getAttribute('aria-checked')).toBe('true')
    host.remove()
  })
})
