"""The symbols a calendar can carry (TIME-4, "Kalender-Symbol wie nexnotes"): the same names as nexnotes' folders,
in the order the picker shows them. The page draws the same names (``frontend/src/lib/symbols.ts``); a test compares
the two lists. Empty is no symbol: the calendar shows its color dot as before."""

from __future__ import annotations

SYMBOLS: tuple[str, ...] = (
    "list", "checklist", "check", "star", "heart", "bookmark", "flag", "bell", "inbox", "folder", "archive", "tag",
    "pin", "lightbulb", "lock", "key", "search", "filter", "target", "sparkles", "bolt", "infinity", "puzzle", "gem",
    "crown", "shield", "link", "paperclip", "trash", "recycle", "info", "question", "warning", "download", "upload",
    "send", "deadline", "house", "sofa", "bed", "bath", "shower", "toilet", "washer", "fridge", "lamp", "cleaning",
    "tools", "wrench", "paint", "plug", "thermometer", "flame", "door", "package", "mail", "shirt", "scissors",
    "armchair", "fan", "blinds", "heater", "aircon", "garage", "shopping-cart", "basket", "bag", "store", "receipt",
    "barcode", "truck", "chef-hat", "food", "coffee", "cooking", "carrot", "fish", "wine", "beer", "takeaway", "water",
    "popcorn", "microwave", "briefcase", "office", "document", "clipboard", "pen", "note", "printer", "chart", "pie",
    "call", "chat", "megaphone", "signature", "id", "authority", "calculator", "presentation", "kanban", "scale",
    "graduation-cap", "book-closed", "books", "backpack", "ruler", "brain", "languages", "lab", "atom", "glasses",
    "binoculars", "notebook", "book", "people", "person", "family", "baby", "smile", "contact", "hand", "thumbsup",
    "invite", "health", "doctor", "hospital", "syringe", "pulse", "bandage", "eye", "ear", "activity", "dumbbell",
    "run", "bike", "ball", "trophy", "medal", "mountain", "tent", "goal", "music", "headphones", "guitar", "mic",
    "game", "dice", "film", "tv", "ticket", "camera", "image", "palette", "theater", "newspaper", "clapper", "piano",
    "disc", "car", "plane", "train", "bus", "ship", "sailboat", "fuel", "parking", "map", "location", "compass",
    "suitcase", "globe", "beach", "route", "tram", "taxi", "charging", "signpost", "hotel", "leaf", "tree", "flower",
    "sun", "moon", "cloud", "rain", "snow", "wind", "umbrella", "drop", "earth", "rainbow", "waves", "bug", "sunrise",
    "sunset", "partlysunny", "storm", "tornado", "paw-print", "dog", "cat", "bird", "rabbit", "turtle", "computer",
    "desktop", "phone", "tablet", "watch", "keyboard", "mouse", "wifi", "server", "chip", "battery", "code",
    "terminal", "database", "settings", "speaker", "qr", "radio", "router", "drive", "branch", "money", "card",
    "wallet", "euro", "growth", "percent", "dollar", "bitcoin", "decline", "calendar", "clock", "alarm", "timer",
    "hourglass", "history", "repeat", "party", "popper", "gift", "circle", "square", "triangle", "hexagon", "plus",
    "hash", "at", "pentagon", "octagon", "arrowright", "arrowup", "refresh", "cross", "minus", "equal",
)


KNOWN = frozenset(SYMBOLS)


def shown(name: str | None) -> str:
    """A stored name as the page gets it: a known symbol, else empty."""
    return name if name in KNOWN else ""
