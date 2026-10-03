import json
from importlib.resources import files

from argospipe.config import CandidateProfile, ModelPrice, Preferences
from argospipe.core.models import JobRecord
from argospipe.llm.provider import Usage

PROMPT_VERSION = "match_v1"
MAX_JOB_CHARS = 6000


class LLMOutputError(Exception):
    def __init__(self, usage: Usage) -> None:
        super().__init__("LLM tool output failed validation twice")
        self.usage = usage


def load_prompt(name: str) -> str:
    return files("argospipe.llm").joinpath("prompts", f"{name}.md").read_text(encoding="utf-8")


def build_match_content(profile: CandidateProfile, preferences: Preferences, job: JobRecord) -> str:
    description = (job.description or "")[:MAX_JOB_CHARS]
    posting = {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": description,
    }
    posting_text = json.dumps(posting, ensure_ascii=False).replace(
        "</job_posting>", "&lt;/job_posting&gt;"
    )
    return (
        f"<profile>\n{profile.model_dump_json()}\n</profile>\n"
        f"<preferences>\n{preferences.model_dump_json()}\n</preferences>\n"
        f"<job_posting>\n{posting_text}\n</job_posting>"
    )


def pricing_error_message(model: str) -> str:
    return (
        f"No price for model {model} in config pricing; the cost cap cannot be enforced without it"
    )


def require_model_pricing(model: str, pricing: dict[str, ModelPrice]) -> None:
    if model not in pricing:
        raise ValueError(pricing_error_message(model))
