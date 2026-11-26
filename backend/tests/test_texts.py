"""The page's sentences for the server's error codes: every code the server sends has one in both
shipped languages, every sentence belongs to a code something still sends, and no API message is the code with its
underscores gone.

The codes are read from the source: ``error("code", ...)``, the typed errors (``AccountError("code")`` and the like),
the middleware's refusals and the sign-in redirects (``?error=code``). A code built at run time is named in ``BUILT``
with where it comes from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models import Account

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
FRONTEND = ROOT / "frontend" / "src"

#: Codes the server sends outside ``error()``, with their source.
BUILT = {
    # middleware.unhandled_error: a full data folder.
    "storage_full",
}

#: Typed errors that carry an error code to the page.
TYPED = "Account|Attachment|Avatar|Backup|Locale|Mail|Nexpaper|Nextasks|Oidc|Token"
RAISED = re.compile(
    rf"""(?:\berror|(?:{TYPED})Error|\brefuse\w*|_to_login|_to_account|_refuse\(\d+,)\(?\s*(?:db,\s*\w+,\s*)?"""
    r'''"([a-z][a-z0-9_]+)"'''
)


#: The shared modules bring their own sentences for their codes (``account.error.*``, ``mail.error.*``, the backup
#: module's ``sicherung`` texts, the key module's ``schluessel`` texts, the languages module's ``sprachen`` texts;
#: checked in their own vitest files) and name
#: other apps in their comments; they are not nextime's to word.
SHARED = {"nexkonten", "nexpost", "nexsicherung", "nexlog", "nexschluessel", "nexsprachen"}


def _sources(folder: Path, suffixes: tuple[str, ...]) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(folder.rglob("*"))
        if path.suffix in suffixes and ".test." not in path.name and "i18n" not in path.parts
        and not SHARED & set(path.parts)
    )


def server_codes() -> set[str]:
    return set(RAISED.findall(_sources(APP, (".py",)))) | BUILT


def texts(language: str) -> dict[str, str]:
    return json.loads((FRONTEND / "i18n" / f"{language}.json").read_text(encoding="utf-8"))["errors"]


def shared_texts(language: str) -> dict[str, str]:
    """The sentences the shared sign-in module brings for its fixed codes (``oidc.error``, ``oidc.formError``): the page
    falls back to them (``lib/errors.ts``)."""
    tree = json.loads((FRONTEND / "vendor" / "nexoidc" / f"oidc.{language}.json").read_text(encoding="utf-8"))["oidc"]
    return {**tree["error"], **tree["formError"]}


def test_the_scan_finds_the_codes_it_should() -> None:
    codes = server_codes()
    # One of each way in: error(), a typed error, a refusal of the middleware, a sign-in redirect.
    assert {"not_found", "email_taken", "client_required", "oidc_only_account", "last_operator", "invalid_time",
            "storage_full"} <= codes
    # A floor: the scan reads the whole server, not one file.
    assert len(codes) > 60


@pytest.mark.parametrize("language", ["de", "en"])
def test_every_code_the_server_sends_has_a_sentence(language: str) -> None:
    assert sorted(server_codes() - set(texts(language)) - set(shared_texts(language))) == []


def test_every_sentence_belongs_to_a_code_still_used() -> None:
    # Sent: a code the scan finds, a quoted code passed to a helper (a default argument, a code a function hands back
    # for a redirect), or one the page asks for itself (``'network'``, ``errorText('name_missing')``).
    backend, frontend = _sources(APP, (".py",)), _sources(FRONTEND, (".ts", ".tsx"))
    # The shared account module raises its codes itself (``vendor/nexkonten``); nextime answers some of them under
    # its older names (``konten_store.OLD_CODES``) and keeps their sentences for pages and scripts of before.
    from app.services.konten_store import OLD_CODES
    from app.vendor.nexkonten import CODES

    shared = set(CODES) | set(OLD_CODES.values())
    # The shared log, backup and languages modules answer nextime's old ways (``/api/logs``, ``/api/backups``,
    # ``/api/locales``) with codes the older pages and scripts know; nextime keeps those sentences (read from the
    # modules' sources).
    modules = "\n".join(path.read_text(encoding="utf-8") for name in ("nexlog", "nexsicherung", "nexsprachen")
                        for path in sorted((APP / "vendor" / name).glob("*.py")))
    shared |= {code for code in texts("de") if f'"{code}"' in modules}
    unused = [code for code in texts("de") if code not in server_codes() and f'"{code}"' not in backend
              and f"'{code}'" not in frontend and f"errors.{code}" not in frontend and code not in shared]
    assert unused == []
    # Of nexlore's leftovers, one hid behind a dictionary key of the same name ("locked" of an account).
    assert "locked" not in texts("de")


def test_no_api_message_is_the_code_spelt_out() -> None:
    source = _sources(APP, (".py",))
    assert ".code.replace(" not in source
    for code, text in re.findall(r'(?:error|AccountError)\(\s*"([a-z_]+)",\s*"([^"]+)"', source):
        if code == "not_found":
            continue
        assert (text[0].isupper() or text.startswith("nextime")) and text.lower().rstrip(".") != code.replace("_", " "), code


def test_no_other_app_speaks_in_the_server() -> None:
    assert re.findall(r"nexcanvas|nexlore|\bboards?\b|Give an address", _sources(APP, (".py",))) == []


def test_the_public_address_refusal_names_no_other_app(client: TestClient, operator: Account) -> None:
    refused = client.put("/api/settings", json={"public_url": "ftp://nowhere"}).json()["detail"]
    assert refused["code"] == "invalid_url"
    assert refused["message"] == "Enter an address starting with http:// or https://."


def test_a_file_that_is_not_there_answers_like_every_not_found(client: TestClient) -> None:
    for path in ("/assets/index-gone.js", "/api/no/such/route"):
        answer = client.get(path)
        assert answer.status_code == 404, path
        assert answer.json() == {"detail": {"code": "not_found", "message": "Not found."}}, path


def test_a_body_too_large_says_the_limit_its_sentence_names(client: TestClient, operator: Account) -> None:
    # "Die Datei ist größer als erlaubt ({{max_mb}} MB)": the guard in front of every route sends the number too,
    # as the upload routes do, so the page never shows the bare placeholder.
    from app.middleware import MAX_BODY

    refused = client.put("/api/settings", content=b"x" * (MAX_BODY + 1), headers={"Content-Type": "application/json"})
    assert refused.status_code == 413
    assert refused.json()["detail"] == {"code": "too_large", "message": "The request is too large.",
                                        "max_mb": MAX_BODY // (1024 * 1024)}


def test_every_refusal_with_a_placeholder_sends_its_value(client: TestClient, operator: Account) -> None:
    # The sentences "at least {{minimum}} characters" and "larger than allowed ({{max_mb}} MB)" need their number from
    # the server; without it the page showed the bare placeholder.
    from app.services import locales

    short = client.put("/api/auth/password", json={"current": "correct horse battery", "new": "eleven char"})
    assert short.status_code == 422 and short.json()["detail"]["minimum"] == 12
    big = client.put("/api/locales/fr", content=b" " * (locales.MAX_BYTES + 1))
    assert big.status_code == 413 and big.json()["detail"]["max_mb"] == 0.5


def test_no_word_of_another_app_is_left_in_the_frame() -> None:
    # Spaces, boards, teams and AI keys belong to nexlore and nexcanvas; nextime has none of them.
    import re

    from app.services import mailer

    app_dir = Path(__file__).resolve().parents[1] / "app"
    foreign = re.compile(r"\bAI (key|list)\b|\bboards?\b|\bteams?\b|Bereich|\{space\}|\bspace=|to the space \"", re.IGNORECASE)
    found = [
        f"{path.relative_to(app_dir)}:{number}: {line.strip()}"
        for path in sorted(app_dir.rglob("*.py")) if "vendor" not in path.parts
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if foreign.search(line)
    ]
    assert found == []
    # The mails come from the shared post module: its invitation names no space either.
    from app.vendor import nexpost

    for language in mailer.MAIL_LANGUAGES:
        invite = nexpost.letters.texts(language)["invite"]
        assert isinstance(invite, dict) and all("space" not in str(text).lower() for text in invite.values())
