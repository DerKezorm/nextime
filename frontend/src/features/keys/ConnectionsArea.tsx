/**
 * Settings › My account › Connections (TIME-25, TIME-26), laid out as nexnotes' area of the same name: nextasks first,
 * nexpaper under it, then the keys for programs.
 */
import { NexpaperSection } from '../nexpaper/NexpaperSection'
import { NextasksSection } from '../nextasks/NextasksSection'
import { KeysSection } from './KeysSection'

export function ConnectionsArea() {
  return (
    <div className="space-y-6" data-testid="connections-area">
      <NextasksSection />
      <NexpaperSection />
      <KeysSection />
    </div>
  )
}
