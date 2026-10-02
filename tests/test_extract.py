import html
import json
import re
from pathlib import Path

import pytest
import yaml

from argospipe.core.extract import extract
from argospipe.core.models import JobRecord

ATS = Path(__file__).parent / "fixtures" / "ats"
EXPECTED_PATH = Path(__file__).parent / "fixtures" / "extract" / "expected.yaml"
EXPECTED = yaml.safe_load(EXPECTED_PATH.read_text())


def _text(value: str) -> str:
    return re.sub(r"<[^>]*>", " ", html.unescape(value))


def _job(title: str, location: str | None = None, description: str | None = None) -> JobRecord:
    return JobRecord(
        fingerprint="fixture",
        company="Fixture",
        title=title,
        location=location,
        description=description,
        first_seen="2026-10-01",
        last_seen="2026-10-01",
    )


def _posting(path: str, index: int) -> JobRecord:
    ats, name = path.split("/")
    body = json.loads((ATS / ats / f"{name}.json").read_text(encoding="utf-8"))
    posting = body[index] if isinstance(body, list) else body["jobs"][index]
    if ats == "greenhouse":
        return _job(posting["title"], posting["location"]["name"], _text(posting["content"]))
    if ats == "ashby":
        return _job(posting["title"], posting["location"], posting["descriptionPlain"])
    parts = [posting["descriptionPlain"]]
    parts.extend(_text(section["content"]) for section in posting["lists"])
    parts.append(posting["additionalPlain"])
    return _job(posting["text"], posting["categories"]["location"], "\n".join(parts))


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_ats_posting(key: str) -> None:
    path, index = key.rsplit(":", 1)
    original = _posting(path, int(index))
    actual = extract(original)
    expected = EXPECTED[key]
    assert len(EXPECTED) == 18
    assert actual is not original
    assert original.seniority is None
    assert actual.seniority == expected["seniority"]
    assert actual.modality == expected["modality"]
    assert set(actual.stack) == set(expected["stack"])
    assert actual.lang == expected["lang"]


@pytest.mark.parametrize(
    ("title", "location", "description", "seniority", "modality", "stack", "lang"),
    [
        (
            "Desarrollador Semi Senior Java - Remoto",
            None,
            None,
            "semi-senior",
            "remote",
            ["Java"],
            None,
        ),
        ("Tech Lead (Híbrido, Buenos Aires)", None, None, "lead", "hybrid", [], None),
        ("Analista Funcional", "Buenos Aires", "Inglés avanzado excluyente", None, None, [], None),
        ("Analista Funcional", None, None, None, None, [], None),
        (
            "Pasante de desarrollo",
            None,
            "Buscamos una persona para aprender con el equipo.",
            "intern",
            None,
            [],
            None,
        ),
        (
            "Developer",
            "Presencial",
            (
                "Buscamos una persona para el equipo de desarrollo de software. "
                "El trabajo requiere experiencia y conocimientos en sistemas, "
                "con tareas para nuestros clientes."
            ),
            None,
            "onsite",
            [],
            "es",
        ),
    ],
)
def test_spanish_cases(
    title: str,
    location: str | None,
    description: str | None,
    seniority: str | None,
    modality: str | None,
    stack: list[str],
    lang: str | None,
) -> None:
    actual = extract(_job(title, location, description))
    assert (actual.seniority, actual.modality, actual.stack, actual.lang) == (
        seniority,
        modality,
        stack,
        lang,
    )


@pytest.mark.parametrize("description", ["Go to market", "C-level", "React quickly"])
def test_ambiguous_words_are_not_technologies(description: str) -> None:
    assert extract(_job("Account Manager", description=description)).stack == []


def test_existing_fields_are_preserved() -> None:
    job = _job("Engineer", description="Remote work with Python")
    job.seniority = "senior"
    job.stack = ["Custom"]
    assert extract(job).seniority == "senior"
    assert extract(job).stack == ["Custom"]
