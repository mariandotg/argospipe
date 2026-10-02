from argospipe.config import CandidateProfile
from argospipe.core.models import JobRecord
from argospipe.core.rank import rank


def job(fingerprint: str, **overrides: object) -> JobRecord:
    return JobRecord.model_validate(
        {
            "fingerprint": fingerprint,
            "company": "Acme",
            "title": "Designer",
            "first_seen": "2026-10-01",
            "last_seen": "2026-10-01",
            **overrides,
        }
    )


def test_rank_uses_stack_role_and_seniority() -> None:
    profile = CandidateProfile(roles=["Backend Engineer"], stack=["Python"], seniority="senior")
    low = job("low", title="Designer", stack=["Figma"], seniority="junior")
    role = job("role", title="Backend Engineer", seniority="junior")
    stack = job("stack", stack=["PYTHON"], seniority="junior")
    close = job("close", stack=["Python"], title="Backend Engineer", seniority="senior")

    top, ranked_out = rank([low, role, stack, close], profile, 2)

    assert top == [close, stack]
    assert [discard.fingerprint for discard in ranked_out] == ["role", "low"]
    assert all(discard.stage == "ranked_out" for discard in ranked_out)
    assert all(discard.reasons[0].startswith("score ") for discard in ranked_out)
    assert all(discard.reasons[0].endswith("below top 2") for discard in ranked_out)


def test_ties_keep_input_order() -> None:
    jobs = [job("first"), job("second"), job("third")]
    top, ranked_out = rank(jobs, CandidateProfile(), 1)
    assert top == [jobs[0]]
    assert [discard.fingerprint for discard in ranked_out] == ["second", "third"]


def test_limit_larger_than_input_keeps_all() -> None:
    jobs = [job("first"), job("second")]
    assert rank(jobs, CandidateProfile(), 5) == (jobs, [])


def test_c_family_languages_are_distinct_when_ranking() -> None:
    profile = CandidateProfile(stack=["C#"])
    cpp, csharp = job("cpp", stack=["C++"]), job("csharp", stack=["C#"])

    top, _ = rank([cpp, csharp], profile, limit=1)

    assert [j.fingerprint for j in top] == ["csharp"]


def test_normalize_stack_keeps_symbols() -> None:
    from argospipe.core.normalize import normalize_stack

    assert normalize_stack(["C", "C++", "C#", ".NET", "Node.js"]) == {
        "c",
        "c++",
        "c#",
        ".net",
        "node.js",
    }
