"""The rules of a series as the interface offers them (TIME-6 AC-1), and their RRULE.

Daily, weekly on chosen days, monthly on the date or on the n-th or last weekday, yearly, each "every n"; ending never,
on a date or after a number of occurrences. A rule from elsewhere that does not fit stays as it is (EC-9): ``read``
gives ``None`` for it, and the series keeps its RRULE until the member picks a new one.

Short months (EC-1, EC-5): "monthly on the 30th" is ``BYMONTHDAY=28,29,30;BYSETPOS=-1`` (the largest of those days the
month has, so its last day when it is shorter), the 31st ``BYMONTHDAY=-1``, the 29th of February yearly
``BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1``. A start on the fifth weekday of a month becomes "the last" (EC-4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import icalendar

WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
FREQUENCIES = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}
MAX_INTERVAL = 999
MAX_COUNT = 9999


class RuleError(ValueError):
    def __init__(self, code: str, field: str) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


@dataclass
class Rule:
    freq: str
    interval: int = 1
    weekdays: tuple[str, ...] = ()
    #: For monthly: "date" (the day of the month), "nth" (the n-th weekday) or "last" (the last weekday).
    monthly: str = "date"
    #: "never", "until" or "count".
    end: str = "never"
    until: date | None = None
    count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"freq": self.freq, "interval": self.interval, "weekdays": list(self.weekdays),
                "monthly": self.monthly, "end": self.end,
                "until": self.until.isoformat() if self.until else None, "count": self.count}


def check(rule: Rule, first: date) -> Rule:
    """The rule when it can be taken; otherwise ``RuleError`` naming the field (EC-8)."""
    if rule.freq not in FREQUENCIES:
        raise RuleError("rule_invalid", "freq")
    if not 1 <= rule.interval <= MAX_INTERVAL:
        raise RuleError("rule_interval", "interval")
    if rule.freq == "weekly":
        days = tuple(day for day in WEEKDAYS if day in set(rule.weekdays))
        if not days or len(days) != len(set(rule.weekdays)):
            raise RuleError("rule_weekdays", "weekdays")
        rule.weekdays = days
    else:
        rule.weekdays = ()
    if rule.monthly not in ("date", "nth", "last"):
        raise RuleError("rule_invalid", "monthly")
    if rule.end == "until":
        if rule.until is None or rule.until < first:
            raise RuleError("rule_until", "until")
        rule.count = None
    elif rule.end == "count":
        if rule.count is None or not 1 <= rule.count <= MAX_COUNT:
            raise RuleError("rule_count", "count")
        rule.until = None
    elif rule.end == "never":
        rule.until = None
        rule.count = None
    else:
        raise RuleError("rule_invalid", "end")
    return rule


def nth_of(day: date) -> int:
    """Which of its weekday in the month ``day`` is: 1 to 5."""
    return (day.day - 1) // 7 + 1


def to_rrule(rule: Rule, start: date | datetime) -> icalendar.vRecur:
    """The RRULE for ``rule`` with a series that begins at ``start`` (aware datetime, or a date for a whole day)."""
    first = start.date() if isinstance(start, datetime) else start
    parts: dict[str, Any] = {"FREQ": FREQUENCIES[rule.freq]}
    if rule.interval > 1:
        parts["INTERVAL"] = rule.interval
    weekday = WEEKDAYS[first.weekday()]
    if rule.freq == "weekly":
        parts["BYDAY"] = list(rule.weekdays)
    elif rule.freq == "monthly":
        if rule.monthly == "date":
            if first.day == 31:
                parts["BYMONTHDAY"] = [-1]
            elif first.day >= 29:
                parts["BYMONTHDAY"] = list(range(28, first.day + 1))
                parts["BYSETPOS"] = [-1]
            else:
                parts["BYMONTHDAY"] = [first.day]
        elif rule.monthly == "nth" and nth_of(first) < 5:
            parts["BYDAY"] = [f"{nth_of(first)}{weekday}"]
        else:
            parts["BYDAY"] = [f"-1{weekday}"]
    elif rule.freq == "yearly" and first.month == 2 and first.day == 29:
        parts["BYMONTH"] = [2]
        parts["BYMONTHDAY"] = [28, 29]
        parts["BYSETPOS"] = [-1]
    if rule.end == "count" and rule.count:
        parts["COUNT"] = rule.count
    elif rule.end == "until" and rule.until:
        parts["UNTIL"] = until_value(rule.until, start)
    return icalendar.vRecur(parts)


def until_value(last_day: date, start: date | datetime) -> date | datetime:
    """UNTIL for a series that may last up to and including ``last_day``: the date for a whole day, otherwise the
    end of that day in the series' zone, in UTC (RFC 5545 wants UTC with a zoned start)."""
    if not isinstance(start, datetime):
        return last_day
    end_of_day = datetime.combine(last_day, time(23, 59, 59))
    if start.tzinfo is None:
        return end_of_day
    return end_of_day.replace(tzinfo=start.tzinfo).astimezone(UTC)


def _listed(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else [value]


def _first(value: Any) -> Any:
    return _listed(value)[0]


def _ints(value: Any) -> list[int]:
    return [int(item) for item in _listed(value)]


def read(recur: icalendar.vRecur | None, start: date | datetime) -> Rule | None:
    """The interface's rule for an RRULE, or ``None`` when it is one nextime does not offer itself."""
    if recur is None:
        return None
    first = start.date() if isinstance(start, datetime) else start
    keys = set(recur.keys())
    freq = {value: key for key, value in FREQUENCIES.items()}.get(str(_first(recur.get("FREQ", ""))).upper())
    if freq is None or keys - {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYSETPOS", "COUNT", "UNTIL",
                               "WKST"}:
        return None
    rule = Rule(freq=freq, interval=int(_first(recur.get("INTERVAL", 1))))
    weekday = WEEKDAYS[first.weekday()]
    byday = [str(day).upper() for day in _listed(recur.get("BYDAY", []))]
    bymonthday = _ints(recur["BYMONTHDAY"]) if "BYMONTHDAY" in recur else []
    bysetpos = _ints(recur["BYSETPOS"]) if "BYSETPOS" in recur else []
    bymonth = _ints(recur["BYMONTH"]) if "BYMONTH" in recur else []
    if freq == "daily":
        if byday or bymonthday or bysetpos or bymonth:
            return None
    elif freq == "weekly":
        if bymonthday or bysetpos or bymonth or any(day not in WEEKDAYS for day in byday):
            return None
        rule.weekdays = tuple(day for day in WEEKDAYS if day in byday) or (weekday,)
    elif freq == "monthly":
        if bymonth:
            return None
        if byday and not bymonthday and not bysetpos and len(byday) == 1:
            if byday[0] == f"-1{weekday}":
                rule.monthly = "last"
            elif byday[0] == f"{nth_of(first)}{weekday}":
                rule.monthly = "nth"
            else:
                return None
        elif byday:
            return None
        elif not bymonthday and not bysetpos or _on_date(first, bymonthday, bysetpos):
            rule.monthly = "date"
        else:
            return None
    elif freq == "yearly":
        if byday:
            return None
        plain = not bymonth and not bymonthday and not bysetpos
        explicit = bymonth == [first.month] and bymonthday == [first.day] and not bysetpos
        leap = first.month == 2 and first.day == 29 and bymonth == [2] and bymonthday == [28, 29] and bysetpos == [-1]
        if not (plain or explicit or leap):
            return None
    if "COUNT" in recur:
        rule.end = "count"
        rule.count = int(_first(recur["COUNT"]))
    elif "UNTIL" in recur:
        until = _first(recur["UNTIL"])
        if isinstance(until, datetime):
            zone = start.tzinfo if isinstance(start, datetime) and start.tzinfo else UTC
            local = (until if until.tzinfo else until.replace(tzinfo=UTC)).astimezone(zone)
            until = local.date()
        rule.end = "until"
        rule.until = until
    return rule


def _on_date(first: date, bymonthday: list[int], bysetpos: list[int]) -> bool:
    """Whether BYMONTHDAY/BYSETPOS say "monthly on the day the series began", short months included."""
    if bymonthday == [first.day] and not bysetpos:
        return True
    if first.day == 31:
        return bymonthday in ([-1], [28, 29, 30, 31]) and bysetpos in ([], [-1])
    return first.day in (29, 30) and bymonthday == list(range(28, first.day + 1)) and bysetpos == [-1]


def shift_weekdays(rule: Rule, days: int) -> Rule:
    """A weekly rule follows its series when the series moves by ``days`` days (Monday and Thursday, moved a day
    later, become Tuesday and Friday)."""
    if rule.freq != "weekly" or days % 7 == 0:
        return rule
    moved = {WEEKDAYS[(WEEKDAYS.index(day) + days) % 7] for day in rule.weekdays}
    rule.weekdays = tuple(day for day in WEEKDAYS if day in moved)
    return rule


def end_before(recur: icalendar.vRecur, cut: date | datetime, start: date | datetime) -> icalendar.vRecur:
    """The same rule, ending before the occurrence at ``cut`` (the old part of a series split there, EC-11)."""
    parts = {key: value for key, value in recur.items() if key not in ("COUNT", "UNTIL")}
    if isinstance(cut, datetime):
        last = cut.astimezone(UTC) - timedelta(seconds=1) if cut.tzinfo else cut - timedelta(seconds=1)
        if isinstance(start, datetime) and start.tzinfo is None:
            last = last.replace(tzinfo=None)
        parts["UNTIL"] = [last]
    else:
        parts["UNTIL"] = [cut - timedelta(days=1)]
    return icalendar.vRecur(parts)
