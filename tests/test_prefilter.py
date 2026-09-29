import pytest

from argospipe.config import Preferences
from argospipe.core.models import JobRecord
from argospipe.core.prefilter import prefilter


def job(**overrides: object) -> JobRecord:
    return JobRecord.model_validate(
        {
            "fingerprint": "job-1",
            "company": "Acme",
            "title": "Backend Engineer",
            "first_seen": "2026-10-01",
            "last_seen": "2026-10-01",
            **overrides,
        }
    )


@pytest.mark.parametrize(
    ("changes", "preferences", "reason"),
    [
        ({"seniority": "junior"}, Preferences(min_seniority="senior"), "Seniority junior"),
        ({"modality": "onsite"}, Preferences(modalities=["remote"]), "Modality onsite"),
        (
            {"location": "Bogotá, Colombia"},
            Preferences(countries=["Argentina"]),
            "outside preferred countries",
        ),
        (
            {"company": "Ácme S.A."},
            Preferences(excluded_companies=["ACME"]),
            "Company Ácme S.A. is excluded",
        ),
    ],
)
def test_each_rule_discards_with_reason(
    changes: dict[str, object], preferences: Preferences, reason: str
) -> None:
    kept, discards = prefilter([job(**changes)], preferences)

    assert kept == []
    assert len(discards) == 1
    assert discards[0].stage == "prefiltered_out"
    assert reason in discards[0].reasons[0]


def test_location_matches_country_without_case_or_accents() -> None:
    offer = job(location="Bogotá, Colombia")
    assert prefilter([offer], Preferences(countries=["colómbia"])) == ([offer], [])


def test_country_match_uses_word_boundaries() -> None:
    offer = job(location="New Zealand")
    assert prefilter([offer], Preferences(countries=["land"]))[0] == []


def test_unknown_fields_do_not_discard() -> None:
    offer = job(company="", location=None, modality=None, seniority=None, stack=[])
    preferences = Preferences(
        min_seniority="senior",
        modalities=["remote"],
        countries=["Argentina"],
        excluded_companies=["Acme"],
    )
    assert prefilter([offer], preferences) == ([offer], [])


def test_remote_job_without_location_passes_country_rule() -> None:
    offer = job(modality="remote", location=None)
    assert prefilter([offer], Preferences(countries=["Argentina"])) == ([offer], [])


def test_multiple_reasons_are_collected_in_one_discard() -> None:
    offer = job(
        company="Acme",
        location="Chile",
        modality="onsite",
        seniority="junior",
    )
    _, discards = prefilter(
        [offer],
        Preferences(
            min_seniority="senior",
            modalities=["remote"],
            countries=["Argentina"],
            excluded_companies=["Acme"],
        ),
    )
    assert len(discards) == 1
    assert len(discards[0].reasons) == 4


def test_empty_preferences_keep_everything_in_input_order() -> None:
    jobs = [job(fingerprint="one", stack=["Python"]), job(fingerprint="two")]
    assert prefilter(jobs, Preferences()) == (jobs, [])


def test_stack_with_nothing_in_common_is_discarded() -> None:
    kept, discards = prefilter(
        [job(stack=["Go", "Rust"])], Preferences(), profile_stack=["Java", "Spring"]
    )

    assert kept == []
    assert "shares nothing" in discards[0].reasons[0]


@pytest.mark.parametrize(
    ("job_stack", "profile_stack"),
    [([], ["Java"]), (["Go"], []), (["Go"], None), (["java"], ["Java"])],
)
def test_stack_rule_passes_when_unknown_or_overlapping(
    job_stack: list[str], profile_stack: list[str] | None
) -> None:
    kept, discards = prefilter([job(stack=job_stack)], Preferences(), profile_stack=profile_stack)

    assert len(kept) == 1
    assert discards == []


def test_c_family_languages_do_not_match_each_other() -> None:
    kept, discards = prefilter([job(stack=["C++"])], Preferences(), profile_stack=["C#"])

    assert kept == []
    assert len(discards) == 1
