from argospipe.config import AtsSourceConfig
from argospipe.sources.companies import companies_as_sources, load_companies


def test_fifty_valid_companies() -> None:
    assert len(load_companies()) == 50


def test_no_duplicate_ats_slug() -> None:
    keys = [(c.ats, c.slug) for c in load_companies()]
    assert len(keys) == len(set(keys))


def test_region_filter() -> None:
    names = {s.name for s in companies_as_sources(["es"])}
    assert "Nubank" not in names
    assert "Celonis" in names


def test_no_filter_returns_all() -> None:
    assert len(companies_as_sources()) == 50


def test_sources_are_ats_configs_with_name() -> None:
    sources = companies_as_sources()
    assert all(isinstance(s, AtsSourceConfig) and s.name for s in sources)
