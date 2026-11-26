"""The operator's own languages: JSON files in ``<data_dir>/locales`` (``NEXTIME_LOCALES_DIR``), from the shared
module sprachen 1.0.0 (``vendor/nexsprachen``, E51).

German and English ship inside the frontend. Every further language is one file named after its code (``es.json``,
``pt-BR.json``) with the keys of the frontend's ``en.json``; what it leaves out falls back to English in the browser.
A file for ``en`` or ``de`` lays its texts over the shipped ones. The folder stays where nextime kept it, so the files
of before count as they are. The module checks everything that comes from outside (code, size, depth, texts, links).

Every name nextime's own module had (``available``, ``load``, ``save``, ``remove``, ``valid_code``, ``Locale``,
``LocaleError``, ``MAX_BYTES`` …) comes from the package.
"""

from __future__ import annotations

from pathlib import Path

from ..config import get_settings
from ..vendor import nexsprachen
from ..vendor.nexsprachen import *  # nextime's names of before


def _folder() -> Path:
    directory = get_settings().locales_dir
    if directory is None:  # the settings validator fills it; this only satisfies the type
        raise RuntimeError("locales_dir is not set")
    return directory


def configure() -> None:
    """What the module needs of nextime: its name, the folder of before, the languages it ships. Done once on import,
    so every caller (the start, the tests) finds it configured."""
    nexsprachen.configure(nexsprachen.AppConfig(app_name="nextime", folder=_folder,
                                                built_in={"de": "Deutsch", "en": "English"}))


configure()


def at_start() -> None:
    """At every start of nextime (``main.lifespan``): the module configured, then what a stopped upload left (hidden
    passing files only) tidied away (``nexsprachen.tidy_at_start``, E52)."""
    configure()
    nexsprachen.tidy_at_start()

