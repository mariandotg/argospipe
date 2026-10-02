from hashlib import sha256

from argospipe.core.normalize import normalize_company, normalize_location, normalize_title


def fingerprint(company: str, title: str, location: str | None) -> str:
    normalized = "|".join(
        (normalize_company(company), normalize_title(title), normalize_location(location) or "")
    )
    return sha256(normalized.encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return sha256(" ".join(text.split()).encode("utf-8")).hexdigest()
