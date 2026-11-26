"""The operator's own languages under ``/api/locales``, the routes of the shared module sprachen 1.0.0 (E51).

Readable without signing in: the sign-in page is shown in the chosen language too, and the texts are the same for
everybody. Adding and removing are the operator's. The paths and the answers of before stay (the list names the
built-in languages too, with ``built_in`` and ``own``; nextime's page passes over the built-in codes).
"""

from __future__ import annotations

from ..deps import require_operator
from ..services import locales  # noqa: F401 - configures the module before its routes are used
from ..vendor.nexsprachen import api

router = api.router(operator=require_operator)
