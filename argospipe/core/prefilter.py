from argospipe.config import Preferences
from argospipe.core.models import Discard, JobRecord
from argospipe.core.normalize import normalize_company, normalize_location

SENIORITY_ORDER = ("intern", "junior", "semi-senior", "senior", "lead", "principal")


def prefilter(
    jobs: list[JobRecord], preferences: Preferences
) -> tuple[list[JobRecord], list[Discard]]:
    kept: list[JobRecord] = []
    discards: list[Discard] = []
    countries = {
        normalized
        for country in preferences.countries
        if (normalized := normalize_location(country))
    }
    excluded_companies = {
        normalized
        for company in preferences.excluded_companies
        if (normalized := normalize_company(company))
    }

    for job in jobs:
        reasons: list[str] = []
        if (
            job.seniority is not None
            and preferences.min_seniority is not None
            and SENIORITY_ORDER.index(job.seniority)
            < SENIORITY_ORDER.index(preferences.min_seniority)
        ):
            reasons.append(
                f"Seniority {job.seniority} is below minimum {preferences.min_seniority}"
            )

        if (
            job.modality is not None
            and preferences.modalities
            and job.modality not in preferences.modalities
        ):
            reasons.append(f"Modality {job.modality} is not in preferred modalities")

        location = normalize_location(job.location)
        if (
            countries
            and location
            and not any(f" {country} " in f" {location} " for country in countries)
        ):
            reasons.append(f"Location {job.location} is outside preferred countries")

        company = normalize_company(job.company)
        if company and company in excluded_companies:
            reasons.append(f"Company {job.company} is excluded")

        if reasons:
            discards.append(
                Discard(fingerprint=job.fingerprint, stage="prefiltered_out", reasons=reasons)
            )
        else:
            kept.append(job)

    return kept, discards
