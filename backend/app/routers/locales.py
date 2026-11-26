"""The operator's own languages under ``/api/locales``, the routes of the shared module sprachen 1.0.0 (E51).

Readable without signing in: the sign-in page is shown in the chosen language too, and the texts are the same for
everybody. Adding and removing are the operator's. The paths and the answers of before stay (the list names the
built-in languages too, with ``built_in`` and ``own``; nextime's page passes over the built-in codes).
"""

from __future__ import annotations

from ..deps import require_operator
from ..services import locales  # noqa: F401 - configures the module before its routes are used
from ..vendor.nexsprachen import api

#: The names nextime's description of the interface had for these routes before the module (``/api/openapi.json``
#: with ``NEXTIME_API_DOCS``): FastAPI's names of nextime's own functions, its summaries, its schema ``LocaleEntry``.
OPERATION_IDS = {
    "list": "list_locales_api_locales_get",
    "read": "read_locale_api_locales__code__get",
    "upload": "upload_locale_api_locales__code__put",
    "remove": "delete_locale_api_locales__code__delete",
}
SUMMARIES = {
    "list": "Languages the operator added",
    "read": "The texts of one added language",
    "upload": "Add or replace a language (operator)",
    "remove": "Remove an added language (operator)",
}
SCHEMA_NAMES = {"LanguageEntry": "LocaleEntry"}

router = api.router(operator=require_operator, operation_ids=OPERATION_IDS, summaries=SUMMARIES,
                    schema_names=SCHEMA_NAMES)
