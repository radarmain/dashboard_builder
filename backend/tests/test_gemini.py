"""Gemini wrappers: never-raise decisions, confident/threshold handling, no input text in logs."""

import logging
from types import SimpleNamespace as NS

import pytest
from app.llm import decisions, gemini_text
from app.llm.decisions import Choice, YesNo
from app.llm.gemini_config import GeminiSettings
from pydantic import SecretStr

SECRET_INPUT = "ZZ-SENSITIVE-INPUT-9931 bar chart of spend"
OPTIONS = {"new_chart": "a new chart", "other": ""}


class FakeDecisions:
    def __init__(self, answers=None, exc=None):
        self.answers, self.exc, self.calls = answers or {}, exc, []

    def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return NS(answers=self.answers, usage=NS(total_input_tokens=50, total_output_tokens=0))


class FakeClient:
    def __init__(self, **kw):
        self.decisions = FakeDecisions(**kw)


def settings(**kw) -> GeminiSettings:
    return GeminiSettings(api_key=SecretStr("k"), **kw)


@pytest.fixture
def use(monkeypatch):
    def _use(client, **kw):
        monkeypatch.setattr(decisions, "get_gemini_settings", lambda: settings(**kw))
        monkeypatch.setattr(decisions, "get_client", lambda *a, **k: client)
        return client

    return _use


class APIErr(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.status_code, self.code = code, code


def test_yes_no_confident_flag(use):
    use(FakeClient(answers={"q": NS(noul=0.9, confidence=0.9)}))
    d = decisions.decide_yes_no(SECRET_INPUT, "Is it?", threshold=0.8)
    assert d and d.answer is True and d.confident and d.p_yes == 0.9
    use(FakeClient(answers={"q": NS(noul=0.3, confidence=0.7)}))
    d = decisions.decide_yes_no(SECRET_INPUT, "Is it?", threshold=0.8)
    assert d and d.answer is False and not d.confident  # 0.7 < 0.8, confident "no" needs 1-p >= thr
    assert d.probabilities["no"] == pytest.approx(0.7)


def test_yes_no_threshold_clamped_into_valid_range(use):
    # 0.99 is the max the API can return, so a threshold of 1.0 would never match; it is clamped.
    use(FakeClient(answers={"q": NS(noul=0.99, confidence=0.99)}))
    d = decisions.decide_yes_no(SECRET_INPUT, "Is it?", threshold=1.0)
    assert d and d.confident
    use(FakeClient(answers={"q": NS(noul=0.5, confidence=0.5)}))
    assert decisions.decide_yes_no(SECRET_INPUT, "Is it?", threshold=0.1).confident  # clamped to .5


def test_choice_min_confidence(use):
    ans = NS(choice="new_chart", confidence=0.65, probabilities={"new_chart": 0.65, "other": 0.35})
    use(FakeClient(answers={"q": ans}))
    assert not decisions.decide_choice(
        SECRET_INPUT, "Which?", OPTIONS, min_confidence=0.7
    ).confident
    assert decisions.decide_choice(SECRET_INPUT, "Which?", OPTIONS, min_confidence=0.6).confident


def test_multiple_questions_one_request_and_payload_shape(use):
    c = use(
        FakeClient(
            answers={
                "intent": NS(
                    choice="other", confidence=0.9, probabilities={"new_chart": 0.1, "other": 0.9}
                ),
                "amb": NS(noul=0.2, confidence=0.8),
            }
        )
    )
    b = decisions.decide(
        SECRET_INPUT,
        {"intent": Choice("Which?", OPTIONS), "amb": YesNo("Ambiguous?", "unclear", "clear")},
    )
    assert b and b["intent"].answer == "other" and b["amb"].answer is False
    assert b.usage.input_tokens == 50 and b.usage.output_tokens == 0
    assert len(c.decisions.calls) == 1
    q = c.decisions.calls[0]["questions"]
    assert q["amb"]["type"] == "noul" and q["amb"]["criteria"]["true_description"] == "unclear"
    assert q["intent"]["type"] == "choice" and q["intent"]["criteria"] == OPTIONS


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("timed out"),
        APIErr(400, "Request blocked due to safety violations " + SECRET_INPUT),
        APIErr(503, "high demand"),
        APIErr(429, "quota"),
        RuntimeError("boom"),
    ],
)
def test_never_raises_on_failures(use, exc):
    use(FakeClient(exc=exc))
    assert decisions.decide_yes_no(SECRET_INPUT, "Is it?") is None
    assert decisions.decide_choice(SECRET_INPUT, "Which?", OPTIONS) is None


def test_malformed_answer_returns_none(use):
    use(FakeClient(answers={"q": NS(choice="not_an_option", confidence=0.9, probabilities={})}))
    assert decisions.decide_choice(SECRET_INPUT, "Which?", OPTIONS) is None
    use(FakeClient(answers={}))  # missing key
    assert decisions.decide_yes_no(SECRET_INPUT, "Is it?") is None


def test_disabled_or_no_client_returns_none_without_calling(use):
    c = use(FakeClient(answers={"q": NS(noul=0.9, confidence=0.9)}), decisions_enabled=False)
    assert decisions.decide_yes_no(SECRET_INPUT, "Is it?") is None
    assert c.decisions.calls == []
    use(FakeClient(answers={"q": NS(noul=0.9, confidence=0.9)}), enabled=False)
    assert decisions.decide_yes_no(SECRET_INPUT, "Is it?") is None
    use(None)  # no key -> get_client returns None
    assert decisions.decide_yes_no(SECRET_INPUT, "Is it?") is None
    assert decisions.decide_yes_no("   ", "Is it?") is None  # empty input


def test_logs_never_contain_input_text(use, caplog):
    caplog.set_level(logging.DEBUG)
    use(FakeClient(answers={"q": NS(noul=0.9, confidence=0.9)}))
    decisions.decide_yes_no(SECRET_INPUT, "Is it?", true_description="DESC-SECRET-1")
    use(FakeClient(exc=APIErr(400, "blocked: " + SECRET_INPUT)))
    decisions.decide_yes_no(SECRET_INPUT, "Is it?")
    use(FakeClient(exc=RuntimeError(SECRET_INPUT)))
    decisions.decide_choice(SECRET_INPUT, "Which?", OPTIONS)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "decision" in text  # we did log something
    for needle in ("ZZ-SENSITIVE", "DESC-SECRET", "spend"):
        assert needle not in text


def test_gemini_text_disabled_raises_typed_error(monkeypatch):
    monkeypatch.setattr(gemini_text, "get_gemini_settings", lambda: settings(enabled=False))
    with pytest.raises(gemini_text.GeminiError) as ei:
        gemini_text.generate_text("hello")
    assert ei.value.reason == "disabled"


def test_gemini_text_tool_round_trip(monkeypatch):
    seen = []

    class FakeInteractions:
        def create(self, **kw):
            seen.append(kw)
            if len(seen) == 1:
                call = NS(
                    type="function_call",
                    id="c1",
                    name="get_x",
                    arguments={"a": 1},
                    model_dump=lambda **k: {
                        "type": "function_call",
                        "id": "c1",
                        "name": "get_x",
                        "arguments": {"a": 1},
                    },
                )
                return NS(
                    steps=[call],
                    output_text="",
                    usage=NS(
                        total_input_tokens=10,
                        total_output_tokens=2,
                        total_thought_tokens=5,
                        total_tokens=17,
                    ),
                )
            return NS(
                steps=[],
                output_text="done",
                usage=NS(
                    total_input_tokens=20,
                    total_output_tokens=3,
                    total_thought_tokens=None,
                    total_tokens=23,
                ),
            )

    monkeypatch.setattr(gemini_text, "get_gemini_settings", lambda: settings())
    monkeypatch.setattr(
        gemini_text, "get_client", lambda *a, **k: NS(interactions=FakeInteractions())
    )
    tool = gemini_text.ToolSpec("get_x", "d", {"type": "object", "properties": {}})
    r = gemini_text.tool_round_trip("hi", [tool], lambda c: {"ok": c.arguments["a"]})
    assert r.text == "done" and r.stop_reason == "end"
    assert r.usage.input_tokens == 30 and r.usage.thinking_tokens == 5
    second = seen[1]["input"]
    assert [s["type"] for s in second] == ["user_input", "function_call", "function_result"]
    assert second[2]["call_id"] == "c1" and seen[1]["store"] is False
