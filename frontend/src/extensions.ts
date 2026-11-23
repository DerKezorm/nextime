/**
 * Where the calendar features dock onto the frame. The frame shows no calendars; a feature adds itself here once, from
 * its own module (imported in `main.tsx` before the first render), and the frame draws it in its place:
 *
 * - `FEATURE_ROUTES`: pages inside the signed-in shell, beside the views Heute, Woche and Monat.
 */
import type { ReactNode } from 'react'

export type FeatureRoute = { path: string; element: ReactNode }

export const FEATURE_ROUTES: FeatureRoute[] = []
