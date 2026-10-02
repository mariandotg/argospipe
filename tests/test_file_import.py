import asyncio
from pathlib import Path

from argospipe.config import FileSourceConfig
from argospipe.sources.file_import import FileSource

FIXTURES = Path(__file__).parent / "fixtures" / "file_import"


def test_csv_records_are_loaded_with_source_defaults() -> None:
    source = FileSource(FileSourceConfig(path=FIXTURES / "jobs.csv"))

    jobs = asyncio.run(source.fetch())

    assert len(jobs) == 1
    assert jobs[0].title == "Backend Engineer"
    assert jobs[0].source == "file"
    assert jobs[0].external_id == "https://jobs.example/acme/backend"
    assert source.errors == []


def test_json_records_are_loaded() -> None:
    source = FileSource(FileSourceConfig(path=FIXTURES / "jobs.json"))

    jobs = asyncio.run(source.fetch())

    assert len(jobs) == 1
    assert jobs[0].title == "Data Engineer"
    assert jobs[0].source == "scraper"
    assert jobs[0].external_id == "data-1"
    assert jobs[0].source_name == "Company site"


def test_missing_optional_columns_use_raw_job_defaults() -> None:
    jobs = asyncio.run(FileSource(FileSourceConfig(path=FIXTURES / "minimal.csv")).fetch())

    assert len(jobs) == 1
    assert jobs[0].location is None
    assert jobs[0].description is None
    assert jobs[0].posted_at is None
    assert jobs[0].source_name is None


def test_missing_required_field_skips_row_and_collects_error() -> None:
    source = FileSource(FileSourceConfig(path=FIXTURES / "missing_required.csv"))

    jobs = asyncio.run(source.fetch())

    assert [job.title for job in jobs] == ["Valid role"]
    assert len(source.errors) == 1
    assert "row 2" in source.errors[0]
    assert "company" in source.errors[0]


def test_empty_file_returns_no_jobs() -> None:
    source = FileSource(FileSourceConfig(path=FIXTURES / "empty.csv"))

    jobs = asyncio.run(source.fetch())

    assert jobs == []
    assert source.errors == []


def test_blank_description_sets_missing_description() -> None:
    jobs = asyncio.run(
        FileSource(FileSourceConfig(path=FIXTURES / "blank_description.json")).fetch()
    )

    assert len(jobs) == 1
    assert jobs[0].missing_description is True


def test_unknown_extension_raises_clear_value_error(tmp_path: Path) -> None:
    source = FileSource(FileSourceConfig(path=tmp_path / "jobs.xlsx"))

    try:
        asyncio.run(source.fetch())
    except ValueError as error:
        assert "expected .csv or .json" in str(error)
    else:
        raise AssertionError("unknown extension did not raise ValueError")
