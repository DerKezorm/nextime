"""Public holidays and school holidays of Germany, Austria and Switzerland, built in (TIME-17 AC-3 to AC-8).

Nothing is fetched: the dates come from the library ``holidays`` (MIT), which ships with nextime's image and knows the
public holidays of every German state, Austrian state and Swiss canton, and the school holidays of the German and
Austrian states. Swiss school holidays are set by the municipalities; there is no reliable list per canton, so the
switch is missing there with a sentence why (EC-13). A year whose school holidays are not published yet shows the
public holidays alone (EC-14).

One source per country (AC-8) with up to two background calendars, "Holidays" and "School holidays". Every holiday
carries its name in German and English and shows in the language of the app (AC-7); one that holds in all chosen
regions comes once, one that holds in only some names them (EC-12).
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

import holidays as holiday_data
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import SOURCE_HOLIDAYS, STATE_OK, Account, Calendar, Source
from . import calendars, ical, live, sync, vault

COUNTRIES: dict[str, dict[str, Any]] = {
    "DE": {"de": "Deutschland", "en": "Germany", "school": True, "regions": {
        "BW": ("Baden-Württemberg", "Baden-Württemberg"), "BY": ("Bayern", "Bavaria"), "BE": ("Berlin", "Berlin"),
        "BB": ("Brandenburg", "Brandenburg"), "HB": ("Bremen", "Bremen"), "HH": ("Hamburg", "Hamburg"),
        "HE": ("Hessen", "Hesse"), "MV": ("Mecklenburg-Vorpommern", "Mecklenburg-Western Pomerania"),
        "NI": ("Niedersachsen", "Lower Saxony"), "NW": ("Nordrhein-Westfalen", "North Rhine-Westphalia"),
        "RP": ("Rheinland-Pfalz", "Rhineland-Palatinate"), "SL": ("Saarland", "Saarland"),
        "SN": ("Sachsen", "Saxony"), "ST": ("Sachsen-Anhalt", "Saxony-Anhalt"),
        "SH": ("Schleswig-Holstein", "Schleswig-Holstein"), "TH": ("Thüringen", "Thuringia")}},
    "AT": {"de": "Österreich", "en": "Austria", "school": True, "regions": {
        "1": ("Burgenland", "Burgenland"), "2": ("Kärnten", "Carinthia"), "3": ("Niederösterreich", "Lower Austria"),
        "4": ("Oberösterreich", "Upper Austria"), "5": ("Salzburg", "Salzburg"), "6": ("Steiermark", "Styria"),
        "7": ("Tirol", "Tyrol"), "8": ("Vorarlberg", "Vorarlberg"), "9": ("Wien", "Vienna")}},
    "CH": {"de": "Schweiz", "en": "Switzerland", "school": False, "regions": {
        "AG": ("Aargau", "Aargau"), "AI": ("Appenzell Innerrhoden", "Appenzell Inner Rhodes"),
        "AR": ("Appenzell Ausserrhoden", "Appenzell Outer Rhodes"), "BE": ("Bern", "Bern"),
        "BL": ("Basel-Landschaft", "Basel-Landschaft"), "BS": ("Basel-Stadt", "Basel-Stadt"),
        "FR": ("Freiburg", "Fribourg"), "GE": ("Genf", "Geneva"), "GL": ("Glarus", "Glarus"),
        "GR": ("Graubünden", "Grisons"), "JU": ("Jura", "Jura"), "LU": ("Luzern", "Lucerne"),
        "NE": ("Neuenburg", "Neuchâtel"), "NW": ("Nidwalden", "Nidwalden"), "OW": ("Obwalden", "Obwalden"),
        "SG": ("St. Gallen", "St. Gallen"), "SH": ("Schaffhausen", "Schaffhausen"), "SO": ("Solothurn", "Solothurn"),
        "SZ": ("Schwyz", "Schwyz"), "TG": ("Thurgau", "Thurgau"), "TI": ("Tessin", "Ticino"), "UR": ("Uri", "Uri"),
        "VD": ("Waadt", "Vaud"), "VS": ("Wallis", "Valais"), "ZG": ("Zug", "Zug"), "ZH": ("Zürich", "Zurich")}},
}
LANGUAGES = {"de": "de", "en": "en_US"}
PUBLIC = "public"
SCHOOL = "school"
#: The years the calendars hold around today: the past one and the next two.
YEARS_BACK, YEARS_AHEAD = 1, 2


def regions(language: str) -> list[dict[str, Any]]:
    """What the dialog offers (AC-3): countries with their regions in the app's language, and whether school
    holidays are known there."""
    index = 0 if language == "de" else 1
    return [{"code": code, "name": info[language], "school": info["school"],
             "regions": [{"code": key, "name": names[index]} for key, names in info["regions"].items()]}
            for code, info in COUNTRIES.items()]


def _years(today: date) -> list[int]:
    return list(range(today.year - YEARS_BACK, today.year + YEARS_AHEAD + 1))


def _region_names(country: str, codes: list[str], language: str) -> str:
    index = 0 if language == "de" else 1
    return ", ".join(COUNTRIES[country]["regions"][code][index] for code in codes)


def _days(country: str, region: str | None, years: list[int], category: str, language: str) -> dict[date, list[str]]:
    found = holiday_data.country_holidays(country, subdiv=region, years=years, language=LANGUAGES[language],
                                          categories=(category,))
    return {day: found.get_list(day) for day in found}


def _collect(country: str, chosen: list[str], years: list[int], category: str) -> list[tuple[date, str, str, list]]:
    """``(day, German name, English name, regions it holds in)``; regions empty when it holds in all chosen."""
    places: list[str | None] = list(chosen) or [None]
    seen: dict[tuple[date, str], dict[str, Any]] = {}
    for place in places:
        german = _days(country, place, years, category, "de")
        english = _days(country, place, years, category, "en")
        for day, names in german.items():
            others = english.get(day, [])
            for position, name in enumerate(names):
                entry = seen.setdefault((day, name), {"en": others[position] if position < len(others) else name,
                                                      "regions": []})
                if place is not None:
                    entry["regions"].append(place)
    out = []
    for (day, name), entry in sorted(seen.items()):
        holds = [code for code in chosen if code in entry["regions"]]
        out.append((day, name, entry["en"], [] if len(holds) == len(chosen) else holds))
    return out


def _object(uid: str, first: date, last: date, german: str, english: str, language: str) -> str:
    parsed = ical.new_calendar()
    import icalendar

    component = icalendar.Event()
    component.add("UID", uid)
    component.add("DTSTAMP", ical.midnight(first))
    component.add("DTSTART", first)
    component.add("DTEND", last + timedelta(days=1))
    component.add("SUMMARY", german if language == "de" else english)
    component.add("X-NEXTIME-SUMMARY-DE", german)
    component.add("X-NEXTIME-SUMMARY-EN", english)
    component.add("TRANSP", "TRANSPARENT")
    parsed.add_component(component)
    return ical.serialize(parsed)


def build(country: str, chosen: list[str], category: str, today: date, language: str) -> dict[str, str]:
    """The calendar's content: one object per holiday, school holidays as spans of days (the same span in several
    chosen regions once)."""
    spans: list[list[Any]] = []
    if category == SCHOOL:
        grouped: dict[tuple[date, date, str], dict[str, Any]] = {}
        for place in chosen or [None]:
            runs: list[list[Any]] = []
            german_days = _days(country, place, _years(today), category, "de")
            english_days = _days(country, place, _years(today), category, "en")
            for day in sorted(german_days):
                name = german_days[day][0]
                english = (english_days.get(day) or [name])[0]
                if runs and runs[-1][2] == name and runs[-1][1] + timedelta(days=1) == day:
                    runs[-1][1] = day
                else:
                    runs.append([day, day, name, english])
            for first, last, name, english in runs:
                entry = grouped.setdefault((first, last, name), {"en": english, "regions": []})
                if place is not None:
                    entry["regions"].append(place)
        for (first, last, name), entry in sorted(grouped.items()):
            where = [] if len(entry["regions"]) == len(chosen) else entry["regions"]
            spans.append([first, last, name, entry["en"], where])
    else:
        spans = [[day, day, german, english, where]
                 for day, german, english, where in _collect(country, chosen, _years(today), category)]
    labelled = []
    for first, last, german, english, where in spans:
        labelled.append((first, last, german + (f" ({_region_names(country, where, 'de')})" if where else ""),
                         english + (f" ({_region_names(country, where, 'en')})" if where else "")))
    spans = labelled
    out = {}
    for first, last, german, english in spans:
        seed = f"{country}:{category}:{first.isoformat()}:{german}"
        uid = f"nextime-{category}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}@nextime"
        out[hashlib.sha256(uid.encode()).hexdigest()[:32] + ".ics"] = _object(uid, first, last, german, english,
                                                                              language)
    return out


def _calendar_name(country: str, category: str, language: str) -> str:
    land = COUNTRIES[country][language]
    if language == "de":
        return f"{'Feiertage' if category == PUBLIC else 'Schulferien'} {land}"
    return f"{'Holidays' if category == PUBLIC else 'School holidays'} {land}"


def set_up(db: Session, account: Account, *, country: str, chosen: list[str], school: bool,
           client_ref: str | None = None) -> Source:
    """"Apply" in "Holidays and school holidays": a new country makes its row, a known one changes (AC-6, AC-8)."""
    from . import events

    if country not in COUNTRIES:
        raise error("invalid_choice", "Choose among the offered countries.", 422, field="country")
    known_regions = COUNTRIES[country]["regions"]
    picked = [code for code in dict.fromkeys(chosen) if code in known_regions]
    if len(picked) != len(set(chosen)):
        raise error("invalid_choice", "Choose among the offered regions.", 422, field="regions")
    school = school and bool(COUNTRIES[country]["school"])
    identity = vault.identity(SOURCE_HOLIDAYS, country)
    row = db.scalar(select(Source).where(Source.account_id == account.id, Source.kind == SOURCE_HOLIDAYS,
                                         Source.identity == identity))
    if row is None:
        row = Source(account_id=account.id, kind=SOURCE_HOLIDAYS, identity=identity, label=country,
                     client_ref=client_ref, state=STATE_OK)
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            row = db.scalar(select(Source).where(Source.account_id == account.id, Source.kind == SOURCE_HOLIDAYS,
                                                 Source.identity == identity))
            if row is None:
                raise
    row.options = {"country": country, "regions": picked, "school": school}
    language = events.language_of(account)
    present = {calendar.remote_url: calendar for calendar in db.scalars(
        select(Calendar).where(Calendar.source_id == row.id))}
    for category, wanted in ((PUBLIC, True), (SCHOOL, school)):
        url = f"holidays:{country}:{category}"
        if wanted and url not in present:
            made = calendars._make(db, account.id, _calendar_name(country, category, language), "gray",
                                   background=True)
            made.source_id, made.remote_url, made.read_only = row.id, url, True
        elif not wanted and url in present:
            live.touch_calendar(db, present[url].id)
            db.delete(present[url])
    db.flush()
    round_(db, row)
    row.synced_at = sync.clock()
    row.due_at = sync.clock() + timedelta(seconds=sync.INTERVALS[SOURCE_HOLIDAYS])
    live.touch_accounts(db, {account.id})
    db.commit()
    return row


def round_(db: Session, source: Source) -> None:
    from . import events

    options = source.options or {}
    country = str(options.get("country") or "")
    if country not in COUNTRIES:
        return
    account = db.get(Account, source.account_id)
    language = events.language_of(account) if account is not None else "en"
    today = sync.clock().date()
    for calendar in db.scalars(select(Calendar).where(Calendar.source_id == source.id)):
        category = calendar.remote_url.rsplit(":", 1)[-1]
        sync.replace_all(db, calendar, build(country, list(options.get("regions") or []), category, today,
                                             language))
