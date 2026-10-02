from hashlib import sha256

from argospipe.core.fingerprint import fingerprint, text_hash


def test_same_offer_across_sources_has_same_fingerprint() -> None:
    linkedin = fingerprint("Acme S.A.", "Sr. Backend Engineer (Remoto)", "Buenos Aires, Argentina")
    getonboard = fingerprint("ACME", "Senior Backend Engineer", "Buenos Aires, Argentina")

    assert linkedin == getonboard
    assert len(linkedin) == 64


def test_different_titles_have_different_fingerprints() -> None:
    engineer = fingerprint("Acme", "Backend Engineer", "Buenos Aires")
    developer = fingerprint("Acme", "Backend Developer", "Buenos Aires")

    assert engineer != developer


def test_fingerprint_hashes_normalized_fields_with_empty_missing_location() -> None:
    expected = sha256(b"acme|tech lead|").hexdigest()

    assert fingerprint("Acme LLC", "Technical Lead - Remote", None) == expected


def test_text_hash_ignores_whitespace_differences() -> None:
    expected = sha256(b"Build Python services.").hexdigest()

    assert text_hash("  Build\n Python\tservices.  ") == expected
    assert text_hash("Build Python services.") == expected
