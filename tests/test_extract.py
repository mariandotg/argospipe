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


# Keys whose true value the extractor cannot reach from title, location and description.
KNOWN_GAPS = {
    "greenhouse/gitlab:1": "stack: the employer name GitLab is matched as a technology",
    "greenhouse/gitlab:2": "stack: the employer name GitLab is matched as a technology",
    "ashby/supabase:0": "stack: the employer name Supabase is matched as a technology",
    "ashby/supabase:1": "stack: the employer name Supabase is matched as a technology",
    "ashby/supabase:2": "stack: the employer name Supabase is matched as a technology",
    "lever/dlocal:0": "modality: hybrid only in Lever workplaceType, not in the text",
    "lever/dlocal:1": "modality: hybrid only in Lever workplaceType, not in the text",
    "lever/dlocal:2": "modality: hybrid only in Lever workplaceType, not in the text",
    "lever/yuno:2": "modality: remote only in Lever workplaceType, description is blank",
}


def _case(key: str) -> object:
    gap = KNOWN_GAPS.get(key)
    return pytest.param(key, marks=pytest.mark.xfail(reason=gap, strict=True)) if gap else key


def test_expected_covers_18_postings() -> None:
    assert len(EXPECTED) == 18


@pytest.mark.parametrize("key", [_case(key) for key in sorted(EXPECTED)])
def test_ats_posting(key: str) -> None:
    path, index = key.rsplit(":", 1)
    original = _posting(path, int(index))
    actual = extract(original)
    expected = EXPECTED[key]
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
            "Desarrollador Semi Senior Java – Remoto",  # noqa: RUF001
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


@pytest.mark.parametrize(
    "description",
    [
        "Please express interest by Friday",
        "Express interest in the role today",
        "We will handle the rest of the process",
        "The internship starts in Spring 2026",
        "Spring 2026 cohort",
        "We move at a swift pace",
        "Swift response times matter",
        "Spark joy in customers",
        "Ruby red gemstones",
    ],
)
def test_common_words_are_not_technologies(description: str) -> None:
    assert extract(_job("Account Manager", description=description)).stack == []


@pytest.mark.parametrize(
    ("description", "stack"),
    [
        ("Experience with Spring Boot services", ["Spring"]),
        ("Build APIs with Express.js", ["Express"]),
        ("Design REST APIs", ["REST"]),
        ("iOS developer using Swift", ["Swift"]),
        ("Data engineer with Apache Spark", ["Spark"]),
        ("Backend developer in Ruby on Rails", ["Ruby", "Rails"]),
        ("Deploy to kubernetes and POSTGRESQL", ["Kubernetes", "PostgreSQL"]),
    ],
)
def test_technologies_with_context_match(description: str, stack: list[str]) -> None:
    assert sorted(extract(_job("Engineer", description=description)).stack) == sorted(stack)


@pytest.mark.parametrize(
    ("tag", "modality"),
    [("#LI-Remote", "remote"), ("#LI-Hybrid", "hybrid"), ("#LI-Onsite", "onsite")],
)
def test_linkedin_tags(tag: str, modality: str) -> None:
    assert extract(_job("Engineer", description=f"About us. {tag}")).modality == modality


@pytest.mark.parametrize("title", ["Backend Lead", "Lead", "Lead Backend Developer"])
def test_bare_lead_in_title(title: str) -> None:
    assert extract(_job(title)).seniority == "lead"


def test_lead_generation_is_not_lead() -> None:
    assert extract(_job("Lead Generation Specialist")).seniority is None


@pytest.mark.parametrize(
    ("description", "seniority"),
    [
        ("Buscamos un desarrollador senior para el equipo", "senior"),
        ("Buscamos una persona semi senior", "semi-senior"),
        ("Perfil semi senior con experiencia", "semi-senior"),
        ("Nivel junior, con ganas de aprender", "junior"),
    ],
)
def test_spanish_seniority_phrases(description: str, seniority: str) -> None:
    assert extract(_job("Developer", description=description)).seniority == seniority


def test_remote_and_hybrid_in_description_is_ambiguous() -> None:
    description = "This is a remote role. We also run a hybrid model in some cities."
    assert extract(_job("Developer", description=description)).modality is None


def test_location_settles_remote_and_hybrid_mentions() -> None:
    description = "This is a remote role. We also run a hybrid model in some cities."
    assert extract(_job("Developer", "Hybrid, Madrid", description)).modality == "hybrid"


def test_existing_fields_are_preserved() -> None:
    job = _job("Engineer", description="Remote work with Python")
    job.seniority = "senior"
    job.stack = ["Custom"]
    assert extract(job).seniority == "senior"
    assert extract(job).stack == ["Custom"]
