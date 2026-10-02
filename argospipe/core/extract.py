"""Conservative, local extraction of job attributes."""

import re
import unicodedata
from functools import lru_cache
from importlib import resources
from typing import cast

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
    found = [level for level, pattern in _SENIORITY if re.search(pattern, normalized_title)]
    if len(found) == 1:
        return found[0]
    if found:
        return None
    text = _plain(description)
    for level, pattern in _SENIORITY:
        prefix = r"\b(?:seeking|hiring|looking for|position|role)\s+(?:an?\s+)?"
        if re.search(rf"{prefix}{pattern}", text):
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
            r"work remotely|#li-remote)\b"
        ),
        "hybrid": r"(?:\bhybrid (?:work|model|role)\b|#li-hybrid\b)",
        "onsite": r"\b(?:on[ -]?site (?:work|role|position)|in[ -]?office|presencial)\b",
    }
    found = [kind for kind, pattern in evidence.items() if re.search(pattern, text)]
    if "hybrid" in found:
        return "hybrid"
    return found[0] if len(found) == 1 else None


@lru_cache(maxsize=1)
def _dictionary() -> dict[str, list[str]]:
    path = resources.files("argospipe.core").joinpath("data/stack_v1.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data["version"] != 1:
        raise ValueError("Unsupported stack dictionary version")
    return cast(dict[str, list[str]], data["technologies"])


def _technology_context(text: str, start: int, end: int) -> bool:
    nearby = text[max(0, start - 65) : min(len(text), end + 65)]
    return bool(
        re.search(
            r"\b(?:languages?|frameworks?|technologies|tech stack|coding|programming|"
            r"developer|engineer|javascript|typescript|vue|angular|python|java|kotlin|rust|"
            r"golang|react\.js)\b",
            nearby,
            re.IGNORECASE,
        )
    )


def _stack(title: str, description: str) -> list[str]:
    text = re.sub(r"https?://\S+", " ", f"{title}\n{description}")
    result = []
    for canonical, aliases in _dictionary().items():
        for alias in (canonical, *aliases):
            token = next(iter(normalize_stack([alias])))
            pattern = rf"(?<![\w+#.]){re.escape(alias)}(?![\w+#])"
            for match in re.finditer(pattern, text, re.IGNORECASE):
                if token in {"c", "go", "react"} and (
                    match.group() != alias or not _technology_context(text, *match.span())
                ):
                    continue
                result.append(canonical)
                break
            if canonical in result:
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
            "stack": job.stack or _stack(job.title, description),
            "lang": job.lang or _language(description),
        }
    )
