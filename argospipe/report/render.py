from pathlib import Path

from argospipe.core.models import RunResult


def render(result: RunResult, path: Path) -> Path:
    raise NotImplementedError
