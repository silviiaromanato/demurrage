import shutil
from pathlib import Path

import pytest

from demurrage import llm
from demurrage import pipeline
from demurrage.models import EmailReview


def _choice(option: str) -> dict:
    return {"type": "choice", "choice": option, "confidence": 0.9, "probabilities": {}}


def _jev_yes_kestral():
    return {
        "voyage": _choice("yes"),
        "vessel": _choice("KESTREL BAY"),
        "contract": _choice("yes"),
        "schedule": _choice("no"),
        "laytime": _choice("no"),
        "position": _choice("no"),
        "other": _choice("no"),
    }


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Minimal inbox under tmp_path; cwd is the workspace root."""
    inbox = tmp_path / "emails"
    inbox.mkdir()
    for name in ("email_01.eml", "email_05.eml"):
        shutil.copy(Path("emails") / name, inbox / name)
    shutil.copy(Path("fleet.json"), tmp_path / "fleet.json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(llm.API_KEY_ENV, "test")
    return tmp_path


@pytest.fixture
def jev_yes(monkeypatch):
    def decide(state, questions):
        replies = _jev_yes_kestral()
        return {qid: replies[qid] for qid in questions}

    monkeypatch.setattr(llm, "decide", decide)


def test_live_writes_outputs_and_check_passes(workspace, jev_yes):
    added = pipeline.run_live()
    assert added == 2
    assert pipeline.EXTRACTIONS_PATH.is_file()
    assert pipeline.RESULT_PATH.is_file()
    assert pipeline.BRIEF_PATH.is_file()
    extractions = pipeline.read_extractions()
    assert len(extractions) == 2
    assert all(e.extraction_status == "complete" for e in extractions)
    assert pipeline.run_check() == []


def test_live_stops_when_blocked_on_open_review(workspace, jev_yes, monkeypatch):
    pipeline.run_live()
    extractions = pipeline.read_extractions()
    extractions[-1] = extractions[-1].model_copy(
        update={
            "voyage_about": "uncertain",
            "review": EmailReview(
                code="VOYAGE_UNCERTAIN",
                prompt="Is it about a voyage?",
                options=["yes", "no"],
                status="open",
            ),
        }
    )
    pipeline.write_extractions(extractions)
    assert pipeline.run_live() == 0
    assert len(pipeline.read_extractions()) == 2


def test_answer_resumes_ingest_after_voyage_question(workspace, monkeypatch):
    replies = _jev_yes_kestral()

    def decide(state, questions):
        if state.get("subject", "").startswith("RE:"):
            return {
                qid: _choice("uncertain") if qid == "voyage" else replies[qid]
                for qid in questions
            }
        return {qid: replies[qid] for qid in questions}

    monkeypatch.setattr(llm, "decide", decide)
    assert pipeline.run_live() == 2
    last = pipeline.read_extractions()[-1]
    assert last.review and last.review.status == "open"
    pipeline.run_answer(last.filename, "yes")
    answered = pipeline.read_extractions()[-1]
    assert answered.voyage_about == "yes"
    assert answered.review is None
    assert pipeline.run_check() == []


def test_batch_replays_prior_human_answers(workspace, monkeypatch):
    replies = _jev_yes_kestral()

    def decide(state, questions):
        if state.get("subject", "").startswith("RE:"):
            return {
                qid: _choice("uncertain") if qid == "voyage" else replies[qid]
                for qid in questions
            }
        return {qid: replies[qid] for qid in questions}

    monkeypatch.setattr(llm, "decide", decide)
    pipeline.run_live()
    pipeline.run_answer("email_05.eml", "yes")
    before = pipeline.read_extractions()
    pipeline.run_batch()
    after = pipeline.read_extractions()
    assert len(after) == len(before)
    assert after[-1].voyage_about == "yes"
    assert pipeline.run_check() == []


def test_run_answer_rejects_unknown_email(workspace, jev_yes):
    pipeline.run_live()
    with pytest.raises(SystemExit, match="No open question"):
        pipeline.run_answer("missing.eml", "yes")


def test_run_check_reports_missing_brief(workspace, jev_yes):
    pipeline.run_live()
    pipeline.BRIEF_PATH.unlink()
    problems = pipeline.run_check()
    assert any("brief.md not found" in p for p in problems)


def test_main_check_exits_on_failure(workspace, jev_yes, monkeypatch):
    pipeline.run_live()
    pipeline.RESULT_PATH.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["demurrage", "check"],
    )
    with pytest.raises(SystemExit) as exc:
        pipeline.main()
    assert exc.value.code == 1
