/**
 * The member's own nextasks in the calendar (TIME-25, `backend/app/routers/nextasks.py`): the connection, the tasks due
 * and ticking them off. The connection and the tasks are held once for the page; each view asks for the days it shows.
 *
 * A task ticked off stays struck through until the page reloads, and a second tap takes it back (AC-9): what was
 * ticked here is kept in memory only, so a reload shows nextasks' state.
 */
import { api } from '../../api/client'
import { onAccountChange } from '../../state/account'

export type NextasksStatus = {
  connected: boolean
  /** The operator allows addresses in the own network ("Verbindungen ins eigene Netz"). */
  local_allowed: boolean
  url?: string
  host?: string
  name?: string
  level?: 'read' | 'write'
  /** "Meine Aufgaben" hidden like a calendar (AC-7). */
  hidden?: boolean
  /** What went wrong last: a key run out, one that only reads, another API (EC-4, EC-5). */
  problem?: string | null
  checked_at?: string | null
}

export type Task = {
  id: string
  title: string
  /** The due day in the member's zone. */
  day: string
  /** "HH:MM" in the member's zone, or null for a task without a time. */
  time: string | null
  link: string
  /** The rule of repetition as nextasks writes it ("FREQ=WEEKLY;..."), or "". */
  repeat: string
  done: boolean
  waiting: boolean
  /** The area (space) of nextasks, its name, the list and the section. */
  area?: string
  area_name?: string
  list_name?: string
  section?: string
  /** The first lines of its note, as text. */
  notes?: string
  assignees?: string[]
  /** A coming day of a repeating task: to look at, not to tick. */
  preview?: boolean
  /** Its list, and the row of the sidebar it belongs to (its area chosen whole, or its list alone). */
  list?: string
  row?: string
  /** The symbol and colour of its list in nextasks. */
  icon?: string
  color?: string
}

/** A row of the sidebar under "nextasks": an area chosen whole or a list chosen alone (`area_name` its area), hidden
 * by its eye or not; `link` opens it in nextasks. */
export type TaskArea = { id: string; name: string; icon: string; color?: string; kind?: 'area' | 'list'; area_name?: string; link?: string; hidden?: boolean }

/** An area of nextasks in the tree of "Verbindungen": chosen whole, or which of its lists alone. */
export type AreaChoice = { id: string; name: string; icon: string; color?: string; whole: boolean; lists: { id: string; name: string; icon?: string; color?: string; chosen: boolean }[] }

export type TaskNotice = { id: number; title: string; problem: string | null }

export type TaskList = {
  connected: boolean
  hidden: boolean
  problem: string | null
  tasks: Task[]
  notices?: TaskNotice[]
  level?: 'read' | 'write'
  areas?: TaskArea[]
}

export type TickAnswer = { id: number; task_id: string; done: boolean; state: 'waiting' | 'sent' | 'failed' | 'dropped'; waiting: boolean; problem: string | null; moved_on?: boolean }

export const nextasksApi = {
  status: () => api<NextasksStatus>('/api/nextasks'),
  connect: (url: string, token: string) => api<NextasksStatus>('/api/nextasks', { method: 'PUT', body: { url, token } }),
  setHidden: (hidden: boolean) => api<NextasksStatus>('/api/nextasks', { method: 'PATCH', body: { hidden } }),
  disconnect: () => api<null>('/api/nextasks', { method: 'DELETE' }),
  tasks: (to: string, from?: string, previews = false) =>
    api<TaskList>('/api/tasks', { query: { to, ...(from ? { from } : {}), ...(previews ? { previews: 'true' } : {}) } }),
  areas: () => api<{ areas: AreaChoice[] }>('/api/nextasks/areas'),
  chooseAreas: (areas: string[], lists: string[]) => api<{ areas: AreaChoice[] }>('/api/nextasks/areas', { method: 'PUT', body: { areas, lists } }),
  hideArea: (id: string, hidden: boolean) => api<null>(`/api/nextasks/areas/${encodeURIComponent(id)}`, { method: 'PATCH', body: { hidden } }),
  tick: (id: string, done: boolean, ref: string, title: string) =>
    api<TickAnswer>(`/api/tasks/${encodeURIComponent(id)}/tick`, { method: 'POST', body: { done, ref, title } }),
  open: (id: string) => api<{ gone: boolean; link: string }>(`/api/tasks/${encodeURIComponent(id)}/open`),
  seen: (id: number) => api<null>(`/api/tasks/notices/${id}/seen`, { method: 'POST' }),
}

// ---- The connection, once for the page ------------------------------------------------------------------------------

let status: NextasksStatus | null = null
let asking: Promise<void> | null = null
const listeners = new Set<() => void>()

function tell(): void {
  for (const listener of [...listeners]) listener()
}

export function onNextasks(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function nextasksStatus(): NextasksStatus | null {
  return status
}

export function setNextasks(next: NextasksStatus | null): void {
  status = next
  tell()
}

export function loadNextasks(): Promise<void> {
  asking ??= nextasksApi
    .status()
    .then((found) => setNextasks(found && typeof found.connected === 'boolean' ? found : { connected: false, local_allowed: false }))
    .finally(() => {
      asking = null
    })
  return asking
}

// ---- The areas, as the last answer named them ---------------------------------------------------------------------------

let areas: TaskArea[] = []

export function taskAreas(): TaskArea[] {
  return areas
}

export function setTaskAreas(next: TaskArea[]): void {
  if (JSON.stringify(next) === JSON.stringify(areas)) return
  areas = next
  tell()
}

// ---- The choice of areas and lists, changed: every view asks again at once --------------------------------------------

let chosenRound = 0

export function choiceRound(): number {
  return chosenRound
}

export function noteChoice(): void {
  chosenRound += 1
  tell()
}

// ---- What was ticked on this page (AC-9, EC-3) -----------------------------------------------------------------------

export type Ticked = { task: Task; done: boolean; waiting: boolean }

/** The tasks ticked on this page, until it reloads; a task that left nextasks' list stays here struck through. */
const ticked = new Map<string, Ticked>()
/** Tasks found gone on opening (EC-7): out of every list on this page. */
const gone = new Set<string>()
let revision = 0

export function tickedTasks(): Map<string, Ticked> {
  return ticked
}

export function goneTasks(): Set<string> {
  return gone
}

export function taskRevision(): number {
  return revision
}

export function noteTick(task: Task, done: boolean, waiting: boolean): void {
  ticked.set(task.id, { task, done, waiting })
  revision += 1
  tell()
}

export function forgetTick(id: string): void {
  ticked.delete(id)
  revision += 1
  tell()
}

export function noteGone(id: string): void {
  gone.add(id)
  ticked.delete(id)
  revision += 1
  tell()
}

/** Taps under way and the moment of the last tap per task (a double click is one tap, EC-10). */
export const taps = { running: new Set<string>(), last: new Map<string, number>() }

/** Everything held of nextasks on this page goes (another account, and the tests between each other). */
export function forgetNextasks(): void {
  status = null
  asking = null
  areas = []
  taps.running.clear()
  taps.last.clear()
  ticked.clear()
  gone.clear()
  revision += 1
  tell()
}

onAccountChange(forgetNextasks)
