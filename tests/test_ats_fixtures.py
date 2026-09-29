import json
from pathlib import Path
from typing import Any

import pytest

ATS_DIR = Path(__file__).parent / "fixtures" / "ats"

GREENHOUSE = ["datadog", "gitlab"]
LEVER = ["dlocal", "yuno"]
ASHBY = ["nubank", "supabase"]


def load(ats: str, name: str) -> Any:
    return json.loads((ATS_DIR / ats / f"{name}.json").read_text())


def nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


@pytest.mark.parametrize("slug", GREENHOUSE)
def test_greenhouse_shape(slug: str) -> None:
    body = load("greenhouse", slug)
    assert isinstance(body["meta"]["total"], int)
    jobs = body["jobs"]
    assert len(jobs) == 3
    for job in jobs:
        assert isinstance(job["id"], int)
        assert nonblank(job["title"])
        assert job["absolute_url"].startswith("https://")
        assert nonblank(job["location"]["name"])
        assert nonblank(job["updated_at"])
        assert nonblank(job["company_name"])
        assert nonblank(job["content"])  # HTML-escaped; needs html.unescape


@pytest.mark.parametrize("slug", LEVER)
def test_lever_shape(slug: str) -> None:
    body = load("lever", slug)
    assert isinstance(body, list)
    assert len(body) == 3
    for job in body:
        assert nonblank(job["id"])
        assert nonblank(job["text"])
        assert job["hostedUrl"].startswith("https://")
        assert job["applyUrl"].startswith("https://")
        assert isinstance(job["createdAt"], int)
        assert nonblank(job["categories"]["location"])
        assert isinstance(job["categories"]["allLocations"], list)
        assert nonblank(job["country"])
        assert job["workplaceType"] in {"remote", "hybrid", "onsite", "unspecified"}
        assert isinstance(job["lists"], list)
        # Any description field can be blank (a posting may have no text at all).
        assert isinstance(job["descriptionPlain"], str)
        assert isinstance(job["description"], str)
    assert any(nonblank(j["descriptionPlain"]) for j in body)


@pytest.mark.parametrize("slug", ASHBY)
def test_ashby_shape(slug: str) -> None:
    body = load("ashby", slug)
    assert nonblank(body["apiVersion"])
    jobs = body["jobs"]
    assert len(jobs) == 3
    for job in jobs:
        assert nonblank(job["id"])
        assert nonblank(job["title"])
        assert job["jobUrl"].startswith("https://")
        assert job["applyUrl"].startswith("https://")
        assert nonblank(job["location"])
        assert job["isRemote"] is None or isinstance(job["isRemote"], bool)
        assert job["workplaceType"] is None or nonblank(job["workplaceType"])
        assert nonblank(job["publishedAt"])
        assert isinstance(job["address"]["postalAddress"], dict)
        assert nonblank(job["descriptionPlain"])
        assert nonblank(job["descriptionHtml"])


def test_ashby_country_when_present() -> None:
    jobs = load("ashby", "nubank")["jobs"]
    assert any(nonblank(j["address"]["postalAddress"].get("addressCountry")) for j in jobs)


def test_lever_fully_blank_description_is_captured() -> None:
    jobs = load("lever", "yuno")
    assert any(
        not j["descriptionPlain"].strip() and not j["descriptionBodyPlain"].strip() for j in jobs
    )


def test_ashby_null_remote_is_captured() -> None:
    jobs = load("ashby", "supabase")["jobs"]
    assert any(j["isRemote"] is None for j in jobs)


def test_empty_boards() -> None:
    assert load("greenhouse", "empty") == {"jobs": [], "meta": {"total": 0}}
    assert load("lever", "empty") == []
    assert load("ashby", "empty") == {"jobs": [], "apiVersion": "1"}


def test_not_found_bodies() -> None:
    assert load("greenhouse", "not_found") == {"status": 404, "error": "Job not found"}
    assert load("lever", "not_found") == {"ok": False, "error": "Document not found"}
    # Ashby 404 body is plain text "Not Found", not JSON.
    text = (ATS_DIR / "ashby" / "not_found.json").read_text()
    assert text == "Not Found"
