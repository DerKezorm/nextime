/**
 * The iPhone and iPad app in the browser since the shared module geraete 0.1.0 (TIME-29): the cards of
 * `vendor/nexgeraete` call the module's browser routes under `/api/geraete` (the old routes under `/api` keep their
 * answers of before for the tests of 0.4.0). nextime's client with its headers; a refusal carries its code and, where
 * the module sends one, `max`.
 */
import { api, ApiError } from '../../api/client'
import type { DeviceRequest } from '../../vendor/nexgeraete'

/** Where nextime mounts the module's browser routes (`routers/app_pairing.py`, `services/geraete.py`). */
export const GERAETE_BASE = '/api/geraete'

/** The way to Server › Connections, where the operator allows the app and new devices (G30). */
export const SERVER_CONNECTIONS = '/settings/server-connections'

export const deviceRequest: DeviceRequest = async <T,>(path: string, init: Parameters<DeviceRequest>[1] = {}) => {
  try {
    return await api<T>(path, init)
  } catch (error) {
    if (error instanceof ApiError) throw Object.assign(error, { max: error.values.max })
    throw error
  }
}
