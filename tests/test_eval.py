import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from argospipe import cli
from argospipe.cli import app
from argospipe.config import (
    CandidateProfile,
    Config,
    ModelPrice,
    Preferences,
    Profile,
    save_profile,
)
from argospipe.core.models import JobRecord
from argospipe.eval import EvalPair, EvalResult, ProviderFactory, load_pairs, run_eval
from argospipe.llm.provider import Usage
from argospipe.llm.schemas import MatchResult

runner = CliRunner()


class FakeProvider:
    def __init__(self, scores: dict[str, int], fail: set[str] | None = None) -> None:
        self.scores = scores
        self.fail = fail or set()
        self.calls = 0

    async def match(
        self, profile: CandidateProfile, preferences: Preferences, job: JobRecord
    ) -> tuple[MatchResult, Usage]:
        self.calls += 1
        if job.title in self.fail:
            raise RuntimeError("boom")
        result = MatchResult(score=self.scores[job.title], seniority_match="match", summary="s")
        return result, Usage(tokens_in=1000, tokens_out=100)

    async def extract_profile(self, cv_text: str):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def pair(pid: str, human: int) -> EvalPair:
    return EvalPair.model_validate(
        {"id": pid, "job": {"title": pid, "company": "Acme"}, "human_score": human}
    )


PAIRS = [pair("a", 90), pair("b", 40), pair("c", 70)]
DEFAULT_PROFILE = Profile()
CONFIG = Config(pricing={"m1": ModelPrice(input_per_mtok=1.0, output_per_mtok=5.0)})


def run(
    factory: ProviderFactory,
    models: list[str],
    pairs: list[EvalPair] = PAIRS,
    profile: Profile | None = DEFAULT_PROFILE,
) -> EvalResult:
    return asyncio.run(run_eval(pairs, models, factory, CONFIG, profile, 70, 2))


def test_mae_and_agreement_exact() -> None:
    provider = FakeProvider({"a": 80, "b": 60, "c": 65})
    result = run(lambda _m: provider, ["m1"])
    m = result.models[0]
    assert m.mae == pytest.approx((10 + 20 + 5) / 3)
    assert m.agreement_pct == pytest.approx(100 * 2 / 3)  # c: 65<70 vs 70>=70
    assert (m.failures, m.tokens_in, m.tokens_out) == (0, 3000, 300)
    assert m.cost_usd == pytest.approx(3000 / 1e6 + 300 * 5 / 1e6)
    assert [r.model_score for r in m.rows] == [80, 60, 65]


def test_two_models_compared() -> None:
    providers = {
        "m1": FakeProvider({"a": 90, "b": 40, "c": 70}),
        "m2": FakeProvider({"a": 50, "b": 40, "c": 70}),
    }
    result = run(lambda m: providers[m], ["m1", "m2"])
    assert [m.model for m in result.models] == ["m1", "m2"]
    assert result.models[0].mae == 0
    assert result.models[0].agreement_pct == 100
    assert result.models[1].mae == pytest.approx(40 / 3)
    assert result.models[1].agreement_pct == pytest.approx(100 * 2 / 3)


def test_provider_failure_counted_not_fatal() -> None:
    provider = FakeProvider({"a": 90, "c": 70}, fail={"b"})
    m = run(lambda _m: provider, ["m1"]).models[0]
    assert m.failures == 1
    assert m.mae == 0
    assert m.agreement_pct == 100
    failed = next(r for r in m.rows if r.id == "b")
    assert failed.model_score is None
    assert failed.error is not None and "boom" in failed.error


def test_unknown_price_is_none() -> None:
    provider = FakeProvider({"a": 90, "b": 40, "c": 70})
    assert run(lambda _m: provider, ["unpriced"]).models[0].cost_usd is None


def test_pair_without_any_profile_fails() -> None:
    provider = FakeProvider({"a": 90})
    m = run(lambda _m: provider, ["m1"], pairs=[pair("a", 90)], profile=None).models[0]
    assert m.failures == 1
    assert provider.calls == 0


def test_example_pairs_load() -> None:
    pairs = load_pairs(Path(__file__).parent.parent / "eval" / "pairs.example.yaml")
    assert len(pairs) == 5
    assert sum(p.profile is not None for p in pairs) == 1


def write_pairs(path: Path) -> None:
    path.write_text(
        "pairs:\n"
        "  - id: a\n    job: {title: a, company: Acme}\n    human_score: 90\n"
        "  - id: b\n    job: {title: b, company: Acme}\n    human_score: 40\n",
        encoding="utf-8",
    )


def test_cli_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    pairs = tmp_path / "pairs.yaml"
    write_pairs(pairs)
    save_profile(Profile())
    monkeypatch.setattr(cli, "AnthropicProvider", lambda _m: FakeProvider({"a": 80, "b": 50}))
    result = runner.invoke(
        app, ["eval", "--pairs", str(pairs), "--model", "claude-haiku-4-5", "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["pairs"] == 2
    assert data["threshold"] == 70
    model = data["models"][0]
    assert model["mae"] == 10
    assert model["agreement_pct"] == 100
    assert len(model["rows"]) == 2


def test_cli_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    pairs = tmp_path / "pairs.yaml"
    write_pairs(pairs)
    monkeypatch.setattr(cli, "AnthropicProvider", lambda _m: FakeProvider({"a": 80, "b": 50}))
    result = runner.invoke(app, ["eval", "--pairs", str(pairs), "--model", "x", "--model", "y"])
    assert result.exit_code == 0, result.output
    assert "n/a" in result.output


def test_cli_missing_key_exits_without_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pairs = tmp_path / "pairs.yaml"
    write_pairs(pairs)
    created: list[str] = []

    def factory(model: str) -> FakeProvider:
        created.append(model)
        return FakeProvider({"a": 80, "b": 50})

    monkeypatch.setattr(cli, "AnthropicProvider", factory)
    result = runner.invoke(app, ["eval", "--pairs", str(pairs), "--model", "x"])
    assert result.exit_code == 1
    assert created == []


def test_cli_malformed_pairs_yaml_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    pairs = tmp_path / "pairs.yaml"
    pairs.write_text("pairs: [unclosed", encoding="utf-8")
    result = runner.invoke(app, ["eval", "--pairs", str(pairs), "--model", "x"])
    assert result.exit_code == 1
    assert "Eval failed" in result.output


def test_cli_missing_pairs_points_to_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARGOSPIPE_HOME", str(tmp_path))
    result = runner.invoke(app, ["eval", "--pairs", str(tmp_path / "nope.yaml"), "--model", "x"])
    assert result.exit_code == 1
    assert "pairs.example.yaml" in result.output
