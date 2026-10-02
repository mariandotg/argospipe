"""Conservative, local extraction of job attributes."""

import re
import unicodedata
from functools import lru_cache
from importlib import resources
from typing import Any, NamedTuple

import yaml

from argospipe.config import Modality, Seniority
from argospipe.core.models import JobRecord
from argospipe.core.normalize import normalize_stack, normalize_title

_SENIORITY: tuple[tuple[Seniority, str], ...] = (
    ("intern", r"\b(?:intern(?:ship)?|trainee|pasante|pasantia)\b"),
    ("principal", r"\b(?:principal|staff)\b"),
    (
        "lead",
        (
            r"\b(?:(?:tech|technical|team)\s+lead|"
            r"lead\s+(?:\w+\s+){0,2}(?:engineer|developer|designer|architect))\b"
        ),
    ),
    ("semi-senior", r"\b(?:semi[ -]?senior|ssr)\b"),
    ("senior", r"(?<!semi )(?<!semi-)(?<!semi)\b(?:senior|sr)\b"),
    ("junior", r"\b(?:junior|jr)\b"),
)
_LEAD_TITLE = r"\blead\b(?!\s+(?:generation|gen|qualification|nurturing|scoring|time)\b)"
_TITLE_SENIORITY = tuple(
    (level, _LEAD_TITLE if level == "lead" else pattern) for level, pattern in _SENIORITY
)
_SENIORITY_PREFIX = (
    r"\b(?:(?:seeking|hiring|looking for|position|role)\s+(?:an?\s+)?|"
    r"buscamos\s+(?:un(?:/a|a)?\s+)?(?:\w+\s+){0,3}?|"
    r"(?:perfil|nivel|seniority)\s+(?:de\s+)?)"
)
_MODALITY: tuple[tuple[Modality, str], ...] = (
    ("remote", r"\b(?:remote|remoto|remota|all-remote|fully remote|100% remote)\b"),
    ("hybrid", r"\b(?:hybrid|hibrido|hibrida)\b"),
    ("onsite", r"\b(?:on[ -]?site|in[ -]?office|presencial)\b"),
)
_ES_WORDS = {
    "el",
    "la",
    "los",
    "las",
    "de",
    "del",
    "en",
    "para",
    "por",
    "con",
    "una",
    "un",
    "que",
    "se",
    "su",
    "sus",
    "y",
    "es",
    "como",
    "esta",
    "este",
    "nuestro",
    "nuestra",
    "experiencia",
    "conocimientos",
    "trabajo",
    "equipo",
}
_EN_WORDS = {
    "the",
    "and",
    "of",
    "to",
    "in",
    "for",
    "with",
    "a",
    "an",
    "you",
    "your",
    "we",
    "our",
    "is",
    "are",
    "this",
    "that",
    "as",
    "on",
    "from",
    "will",
    "have",
    "experience",
    "team",
    "work",
}


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _seniority(title: str, description: str) -> Seniority | None:
    normalized_title = normalize_title(title)
    found = [level for level, pattern in _TITLE_SENIORITY if re.search(pattern, normalized_title)]
    if len(found) == 1:
        return found[0]
    if found:
        return None
    text = _plain(description)
    for level, pattern in _SENIORITY:
        if re.search(rf"{_SENIORITY_PREFIX}{pattern}", text):
            return level
    return None


def _modality(title: str, location: str | None, description: str) -> Modality | None:
    for source in (title, location or ""):
        found = [kind for kind, pattern in _MODALITY if re.search(pattern, _plain(source))]
        if len(found) == 1:
            return found[0]
    text = _plain(description)
    evidence: dict[Modality, str] = {
        "remote": (
            r"\b(?:fully remote|all-remote|remote (?:work|role|position)|"
            r"work remotely)\b|(?<!\w)#li-remote\b"
        ),
        "hybrid": r"\bhybrid (?:work|model|role)\b|(?<!\w)#li-hybrid\b",
        "onsite": (
            r"\b(?:on[ -]?site (?:work|role|position)|in[ -]?office|presencial)\b|"
            r"(?<!\w)#li-on-?site\b"
        ),
    }
    kinds = {kind for kind, pattern in evidence.items() if re.search(pattern, text)}
    if kinds == {"hybrid", "onsite"}:
        return "hybrid"
    return next(iter(kinds)) if len(kinds) == 1 else None


class _Dictionary(NamedTuple):
    technologies: dict[str, list[str]]
    case_sensitive: frozenset[str]
    context_required: frozenset[str]
    alias_only: frozenset[str]
    generic: frozenset[str]


@lru_cache(maxsize=1)
def _dictionary() -> _Dictionary:
    path = resources.files("argospipe.core").joinpath("data/stack_v2.yaml")
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data["version"] != 2:
        raise ValueError("Unsupported stack dictionary version")
    return _Dictionary(
        data["technologies"],
        frozenset(data["case_sensitive"]),
        frozenset(data["context_required"]),
        frozenset(data["alias_only"]),
        frozenset(data["generic"]),
    )


def generic_technologies() -> frozenset[str]:
    return frozenset(normalize_stack(list(_dictionary().generic)))


def _technology_context(text: str, start: int, end: int) -> bool:
    sentence_start = max(text.rfind("\n", 0, start), text.rfind(". ", 0, start)) + 1
    breaks = [i for i in (text.find("\n", end), text.find(". ", end)) if i != -1]
    sentence_end = min(breaks) if breaks else len(text)
    window_start = max(sentence_start, start - 65)
    nearby = text[window_start : min(sentence_end, end + 65)]
    return bool(
        re.search(
            r"\b(?:languages?|frameworks?|technologies|tech stack|coding|programming|"
            r"developer|engineer|javascript|typescript|vue|angular|python|java|kotlin|rust|"
            r"golang|react\.js|ios|android|scala|hadoop|databricks|flutter|rails)\b",
            nearby,
            re.IGNORECASE,
        )
    )


def _stack(title: str, description: str, company: str = "") -> list[str]:
    text = re.sub(r"https?://\S+", " ", f"{title}\n{description}")
    rules = _dictionary()
    employer = re.sub(r"\W", "", company.casefold())
    result = []
    for canonical, aliases in rules.technologies.items():
        if employer and re.sub(r"\W", "", canonical.casefold()) == employer:
            continue
        flags = 0 if canonical in rules.case_sensitive else re.IGNORECASE
        for alias in (canonical, *aliases):
            bare = alias == canonical
            if bare and canonical in rules.alias_only:
                continue
            needs_context = bare and canonical in rules.context_required
            pattern = rf"(?<![\w+#.]){re.escape(alias)}(?![\w+#])"
            if any(
                not needs_context or _technology_context(text, *match.span())
                for match in re.finditer(pattern, text, flags)
            ):
                result.append(canonical)
                break
    return result


def _language(description: str) -> str | None:
    words = re.findall(r"\b[a-záéíóúñ]+\b", _plain(description))
    if len(words) < 20:
        return None
    es = sum(word in _ES_WORDS for word in words)
    en = sum(word in _EN_WORDS for word in words)
    if max(es, en) < 5 or abs(es - en) < 3:
        return None
    return "es" if es > en else "en"


def extract(job: JobRecord) -> JobRecord:
    description = job.description or ""
    return job.model_copy(
        update={
            "seniority": job.seniority or _seniority(job.title, description),
            "modality": job.modality or _modality(job.title, job.location, description),
            "stack": job.stack or _stack(job.title, description, job.company),
            "lang": job.lang or _language(description),
        }
    )
