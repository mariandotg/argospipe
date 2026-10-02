import pytest

from argospipe.core.normalize import normalize_company, normalize_location, normalize_title


@pytest.mark.parametrize(
    "company",
    [
        "Acme S.A.",
        "ACME SA",
        "Acme S.R.L.",
        "Acme SRL",
        "Acme Inc",
        "Acme LLC",
        "Acme Ltd",
        "Acme GmbH",
        "Acme S.L.",
    ],
)
def test_company_drops_legal_suffix(company: str) -> None:
    assert normalize_company(company) == "acme"


def test_company_collapses_accents_punctuation_and_whitespace() -> None:
    assert normalize_company("  Ácme,   Labs!  ") == "acme labs"


@pytest.mark.parametrize(
    "title",
    ["Tech Lead (Remote)", "Technical Lead - Remote", "Technical Lead | Remoto"],
)
def test_title_unifies_lead_and_remote_suffix(title: str) -> None:
    assert normalize_title(title) == "tech lead"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Sr. Backend Engineer (Remoto)", "senior backend engineer"),
        ("Ssr Backend Developer | LATAM", "semi-senior backend developer"),
        ("Semi-Senior Developer - Argentina", "semi-senior developer"),
        ("Jr Engineer (Híbrido)", "junior engineer"),
    ],
)
def test_title_normalizes_seniority_and_suffixes(title: str, expected: str) -> None:
    assert normalize_title(title) == expected


def test_title_keeps_developer_and_engineer_distinct() -> None:
    assert normalize_title("Backend Developer") != normalize_title("Backend Engineer")


def test_title_keeps_unknown_parenthesized_detail() -> None:
    assert normalize_title("Engineer (Python)") == "engineer python"


def test_location_normalizes_text_and_preserves_none() -> None:
    assert normalize_location("  Búenos Aires,   Argentina  ") == "buenos aires argentina"
    assert normalize_location(None) is None
