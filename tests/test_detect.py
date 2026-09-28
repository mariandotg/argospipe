import pytest

from argospipe.sources.detect import UnsupportedURLError, detect


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("boards.greenhouse.io/acme", ("greenhouse", "acme")),
        (
            "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123/",
            ("greenhouse", "acme"),
        ),
        ("www.job-boards.greenhouse.io/acme/", ("greenhouse", "acme")),
        ("jobs.lever.co/acme", ("lever", "acme")),
        ("https://jobs.lever.co/acme/jobs/123?source=careers", ("lever", "acme")),
        ("www.jobs.eu.lever.co/acme/", ("lever", "acme")),
        ("jobs.ashbyhq.com/acme", ("ashby", "acme")),
        ("https://www.jobs.ashbyhq.com/acme/jobs/123/", ("ashby", "acme")),
        ("jobs.ashbyhq.com/acme?department=engineering", ("ashby", "acme")),
    ],
)
def test_detect_supported_urls(url: str, expected: tuple[str, str]) -> None:
    assert detect(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://boards.greenhouse.io/",
        "https://boards.greenhouse.io.attacker.example/acme",
        "https://example.com/acme",
        "ftp://jobs.lever.co/acme",
        "https://jobs.ashbyhq.com",
    ],
)
def test_detect_rejects_unsupported_urls(url: str) -> None:
    with pytest.raises(UnsupportedURLError, match="Unsupported careers URL"):
        detect(url)
