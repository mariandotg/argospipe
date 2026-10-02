from collections.abc import Callable
from pathlib import Path

from pypdf import PdfReader

from argospipe.config import CandidateProfile, Profile, save_profile
from argospipe.llm.provider import LLMProvider, Usage


def read_cv_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    else:
        text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"No text found in CV: {path}")
    return text


async def import_profile(
    cv_path: Path,
    provider: LLMProvider,
    out_path: Path,
    force: bool,
    on_usage: Callable[[Usage], None] | None = None,
) -> Profile:
    if out_path.exists() and not force:
        raise FileExistsError(f"Profile already exists: {out_path}. Use --force to overwrite it.")
    cv_text = read_cv_text(cv_path)
    extraction, usage = await provider.extract_profile(cv_text)
    profile = Profile(profile=CandidateProfile.model_validate(extraction.model_dump()))
    if out_path.exists() and not force:
        raise FileExistsError(f"Profile already exists: {out_path}. Use --force to overwrite it.")
    save_profile(profile, out_path)
    if on_usage is not None:
        on_usage(usage)
    return profile
