# Third-party licences

nextime itself is licensed under the **GNU Affero General Public License v3.0** (see [LICENSE](LICENSE)). This file
lists what it ships or depends on.

## Bundled with the app

These travel inside the container image.

| What | Copyright | Licence |
|---|---|---|
| Font Inter (`@fontsource-variable/inter`), the font of the interface | The Inter Project Authors | OFL-1.1 |
| Lucide icons (`lucide-react`, partly from Feather), the symbols of the interface and of calendars | Lucide Icons and Contributors; Cole Bemis | ISC, MIT |
| Swagger UI (`swagger-ui-dist`), the page of the API description at `/api/v1/docs`, copied into `api-docs/` of the built interface with its `LICENSE` and `NOTICE` | SmartBear Software | Apache-2.0 |

nextime loads no font, icon or script from another host.

## Services nextime asks, and only when used

| What | For | Terms |
|---|---|---|
| [Photon](https://github.com/komoot/photon) at photon.komoot.io (or the one in `NEXTIME_PLACES_URL`) | suggestions while typing a place; the server asks, never the browser, and only the typed place goes out | Photon is Apache-2.0; its data is © OpenStreetMap contributors, ODbL-1.0 |
| Map tiles of [OpenStreetMap](https://www.openstreetmap.org/copyright) (or the ones in `NEXTIME_TILES_URL`) | the small map of a place, fetched by the server | © OpenStreetMap contributors, ODbL-1.0; the map shows "© OpenStreetMap", and the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/) applies |

The operator can switch suggestions and maps off. Public and school holidays come from the `holidays` package inside
the image; no service is asked for them.

## Backend

| Package | Licence |
|---|---|
| FastAPI, SQLAlchemy, pydantic, pydantic-settings, argon2-cffi, PyJWT, holidays | MIT |
| uvicorn, httpx, segno, lxml | BSD-3-Clause |
| icalendar | BSD-2-Clause |
| recurring-ical-events | LGPL-3.0-or-later |
| python-dateutil | Apache-2.0 **or** BSD-3-Clause |
| caldav | GPL-3.0-or-later **or** Apache-2.0 |
| Pillow | MIT-CMU |
| python-multipart, tzdata | Apache-2.0 |
| cryptography | Apache-2.0 **or** BSD-3-Clause |
| pypdfium2, for the pages of an attached PDF | Apache-2.0 **or** BSD-3-Clause; the PDFium inside BSD-3-Clause, see below |
| pillow-heif | BSD-3-Clause for its own code; the binary wheels are **GPLv2** as a whole, see below |

### PDF pages: pypdfium2 and PDFium

pypdfium2 bundles a build of PDFium (BSD-3-Clause) with the libraries PDFium uses, among them FreeType, HarfBuzz, ICU,
Little CMS, libjpeg-turbo, OpenJPEG, libpng, libtiff and zlib, each under its own permissive licence. The wheel ships
every notice in `pypdfium2-*.dist-info/licenses/`.

### HEIC photos: pillow-heif and the libraries it brings

To read pictures taken on an iPhone (HEIC), nextime uses pillow-heif. Its binary wheels, and so the container image,
contain:

| Library | Licence |
|---|---|
| libheif | LGPL-3.0 |
| libde265 | LGPL-3.0 |
| x265 | GPL-2.0-or-later |

The wheel ships these notices in `pillow_heif-*.dist-info/licenses/`. GPL-2.0-or-later and LGPL-3.0 may be combined
with an AGPL-3.0 work, and the source of every part is public.

## Frontend

| Package | Licence |
|---|---|
| React, React DOM, React Router, i18next, react-i18next | MIT |
| lucide-react | ISC |
| `@fontsource-variable/inter` | OFL-1.1 (the font, see above) |
| swagger-ui-dist | Apache-2.0 |

Build and test tools (Vite, TypeScript, Tailwind CSS, ESLint, Vitest, jsdom, ruff, pytest) are not part of the image.

## Programs in the image

Besides Python from the `python:3.14-slim` image, nextime installs three Debian packages: curl (curl licence, for the
health check), gosu (Apache-2.0, to drop root at the start) and tzdata (public domain, the time zones by name).

## Compatibility

All of the above may be combined into an AGPL-3.0 work. The obligation runs one way: nextime as a whole is AGPL-3.0,
and anyone who runs a modified version as a network service must offer its source.
