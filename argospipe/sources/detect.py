from typing import Literal, cast
from urllib.parse import urlsplit


class UnsupportedURLError(ValueError):
    """The URL does not point to a supported ATS job board."""


_ATS_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
}


def detect(url: str) -> tuple[Literal["greenhouse", "lever", "ashby"], str]:
    """Return the ATS and board slug from a supported careers URL."""
    candidate = url.strip()
    if not candidate:
        raise UnsupportedURLError("Unsupported careers URL: URL is empty.")
    if "://" not in candidate:
        candidate = f"https://{candidate.lstrip('/')}"

    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
    except ValueError as exc:
        raise UnsupportedURLError(f"Unsupported careers URL: {url!r}.") from exc

    if parsed.scheme.lower() not in {"http", "https"} or host is None:
        raise UnsupportedURLError(f"Unsupported careers URL: {url!r}.")

    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    ats = _ATS_HOSTS.get(host)
    slug = next((part for part in parsed.path.split("/") if part), None)
    if ats is None or slug is None:
        supported_hosts = ", ".join(_ATS_HOSTS)
        raise UnsupportedURLError(
            f"Unsupported careers URL: {url!r}. Use a job board on {supported_hosts}."
        )

    return cast(Literal["greenhouse", "lever", "ashby"], ats), slug
