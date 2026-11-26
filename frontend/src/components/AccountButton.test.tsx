/** The account pages' buttons take kind, symbol and size from the module's table (konten 1.1.0, E39): a small one of
 * the module ("Abmelden" of a session, "Passkey entfernen") stays small in nextime as in nexnotes (gallery). */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import '../i18n'
import { BUTTONS, buttonSize, type ButtonName } from '../vendor/nexkonten/konten'
import { AccountButton } from './AccountButton'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let box: HTMLDivElement
let root: Root

beforeEach(() => {
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
})

afterEach(() => {
  act(() => root.unmount())
  box.remove()
})

describe('AccountButton', () => {
  it('passes the size of the table of the module on: sm stays sm, md stays md', () => {
    const names = Object.keys(BUTTONS) as ButtonName[]
    const small = names.filter((name) => buttonSize(name) === 'sm')
    expect(small).toContain('account.passkeyRemove')
    act(() =>
      root.render(
        <>
          {names.map((name) => (
            <AccountButton key={name} name={name} testId={`button-${name}`} />
          ))}
        </>,
      ),
    )
    for (const name of names) {
      const button = box.querySelector<HTMLElement>(`[data-testid="button-${name}"]`)!
      expect([name, button.dataset.size, button.dataset.kind]).toEqual([name, buttonSize(name), BUTTONS[name].kind])
    }
  })
})
