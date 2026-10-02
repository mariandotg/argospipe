import re
import unicodedata

LEGAL_SUFFIXES = frozenset({"s a", "sa", "s r l", "srl", "inc", "llc", "ltd", "gmbh", "s l", "sl"})
TITLE_SUFFIXES = frozenset(
    {
        "remote",
        "remoto",
        "hybrid",
        "hibrido",
        "latam",
        "argentina",
        "buenos aires",
    }
)
TITLE_SYNONYMS = (
    ("technical lead", "tech lead"),
    ("semi senior", "semi-senior"),
    ("ssr", "semi-senior"),
    ("sr", "senior"),
    ("jr", "junior"),
)


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w\s]|_", " ", value).split())


def normalize_company(company: str) -> str:
    normalized = _normalize_text(company)
    for suffix in sorted(LEGAL_SUFFIXES, key=len, reverse=True):
        if normalized.endswith(f" {suffix}"):
            return normalized[: -(len(suffix) + 1)]
    return normalized


def _is_title_suffix(value: str) -> bool:
    parts = re.split(r"[,/&]+", value)
    return all(_normalize_text(part) in TITLE_SUFFIXES for part in parts)


def normalize_title(title: str) -> str:
    while True:
        parenthesized = re.search(r"\s*\(([^()]*)\)\s*$", title)
        separated = re.search(r"(?:\s+-\s+|\s*\|\s*)([^|]+)$", title)
        suffix = parenthesized or separated
        if suffix is None or not _is_title_suffix(suffix.group(1)):
            break
        title = title[: suffix.start()]

    normalized = _normalize_text(title)
    for synonym, canonical in TITLE_SYNONYMS:
        normalized = re.sub(rf"\b{re.escape(synonym)}\b", canonical, normalized)
    return normalized


def normalize_location(location: str | None) -> str | None:
    return None if location is None else _normalize_text(location)
