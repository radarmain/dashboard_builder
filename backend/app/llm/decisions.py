"""Thin wrapper over the Gemini Decisions API (client.decisions.create, model gemini-decision-1).

The Decisions API answers typed questions (yes/no, choice) over shared input and returns
calibrated probabilities with zero output tokens. Use it to route/triage, never to generate.

SECURITY: the input is sent to a third party and is treated as UNTRUSTED text (a user message
can argue for its own classification). Callers must send ONLY sanitized metadata: chat messages,
names, descriptions, shelf/field structure. NEVER row values, credentials, Tableau view data or
anything from BigQuery results. Never act on a decision for something high-stakes without a
second check; always keep a fallback path.

NEVER RAISES: on timeout, API error, safety block (HTTP 400), missing key, or
RDB_DECISIONS_ENABLED=false every function returns None and the caller falls back to its normal
behavior. Logs carry metadata only (question keys, chosen option key, confidence, latency, token
counts); never the input text, descriptions, or exception messages (APIs may echo the input).

Yes/no probabilities are always within 0.01..0.99, so a yes/no confidence (max(p, 1-p)) is within
0.50..0.99. Yes/no thresholds are clamped into that range (a threshold above 0.99 can never match).
"""

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.llm.gemini_client import get_client
from app.llm.gemini_config import get_gemini_settings

log = logging.getLogger("rdb.llm.decisions")

MAX_INPUT_CHARS = 8000
YES_NO_MIN, YES_NO_MAX = 0.5, 0.99
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True)
class YesNo:
    instructions: str
    true_description: str | None = None
    false_description: str | None = None
    threshold: float = 0.8  # on confidence = max(p_yes, 1 - p_yes); effective range 0.5..0.99


@dataclass(frozen=True)
class Choice:
    instructions: str
    options: Mapping[str, str]  # option key -> short criteria ("" if self-explanatory)
    min_confidence: float = 0.7


Question = YesNo | Choice


@dataclass(frozen=True)
class Decision:
    kind: str  # "yes_no" | "choice"
    answer: bool | str  # yes/no -> True/False; choice -> option key
    probabilities: dict[str, float]  # yes/no -> {"yes": p, "no": 1-p}
    confidence: float
    confident: bool  # confidence >= the threshold passed for this question

    @property
    def p_yes(self) -> float | None:
        return self.probabilities.get("yes") if self.kind == "yes_no" else None


@dataclass(frozen=True)
class DecisionUsage:
    input_tokens: int = 0
    output_tokens: int = 0  # always 0 for the Decisions API


@dataclass(frozen=True)
class DecisionBatch:
    answers: dict[str, Decision]
    usage: DecisionUsage = field(default_factory=DecisionUsage)
    latency_ms: int = 0

    def __getitem__(self, key: str) -> Decision:
        return self.answers[key]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _build_questions(questions: Mapping[str, Question]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, q in questions.items():
        if not _KEY_RE.fullmatch(key):
            raise ValueError("bad question key")
        if isinstance(q, YesNo):
            body: dict = {"type": "noul", "instructions": q.instructions}
            if q.true_description or q.false_description:
                body["criteria"] = {
                    "true_description": q.true_description or "",
                    "false_description": q.false_description or "",
                }
        else:
            if len(q.options) < 2:
                raise ValueError("choice needs at least 2 options")
            body = {"type": "choice", "instructions": q.instructions, "criteria": dict(q.options)}
        out[key] = body
    return out


def _parse(key: str, q: Question, raw: object) -> Decision:
    conf = getattr(raw, "confidence", None)
    if isinstance(q, YesNo):
        p = float(raw.noul)  # type: ignore[attr-defined]
        conf = float(conf) if conf is not None else max(p, 1 - p)
        thr = q.threshold
        if not (YES_NO_MIN <= thr <= YES_NO_MAX):
            log.warning("yes/no threshold %.3f for %s outside 0.5..0.99; clamped", thr, key)
            thr = _clamp(thr, YES_NO_MIN, YES_NO_MAX)
        return Decision("yes_no", p >= 0.5, {"yes": p, "no": 1 - p}, conf, round(conf, 6) >= thr)
    choice = str(raw.choice)  # type: ignore[attr-defined]
    if choice not in q.options:
        raise ValueError("answer not in options")
    probs = {str(k): float(v) for k, v in dict(raw.probabilities).items()}  # type: ignore[attr-defined]
    conf = float(conf) if conf is not None else probs.get(choice, 0.0)
    return Decision("choice", choice, probs, conf, round(conf, 6) >= q.min_confidence)


def _summary(batch: dict[str, Decision]) -> str:
    # Chosen option key / yes-no + confidence only. No input text, no descriptions.
    return ",".join(f"{k}={d.answer}@{d.confidence:.2f}" for k, d in batch.items())


def decide(input_text: str, questions: Mapping[str, Question]) -> DecisionBatch | None:
    """Ask several independent questions about the same (sanitized, untrusted) input in ONE call.

    Returns None on any failure or when disabled; never raises. See the module docstring.
    """
    keys = list(questions)
    started = time.perf_counter()
    try:
        s = get_gemini_settings()
        if not (s.enabled and s.decisions_enabled):
            return None
        text = (input_text or "").strip()[:MAX_INPUT_CHARS]
        if not text or not questions:
            log.warning("decision skipped: empty input or no questions keys=%s", keys)
            return None
        client = get_client(s.decisions_timeout_seconds, attempts=1)
        if client is None:
            return None
        body = _build_questions(questions)
        resp = client.decisions.create(
            model=s.decision_model,
            input=text,
            questions=body,
            timeout=s.decisions_timeout_seconds,
        )
        raw_answers = getattr(resp, "answers", None) or {}
        answers = {k: _parse(k, questions[k], raw_answers[k]) for k in keys}
        usage = getattr(resp, "usage", None)
        batch = DecisionBatch(
            answers=answers,
            usage=DecisionUsage(
                input_tokens=int(getattr(usage, "total_input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "total_output_tokens", 0) or 0),
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        log.info(
            "decision ok keys=%s results=%s latency_ms=%d in_tokens=%d out_tokens=%d",
            keys,
            _summary(answers),
            batch.latency_ms,
            batch.usage.input_tokens,
            batch.usage.output_tokens,
        )
        return batch
    except Exception as e:  # noqa: BLE001 - decisions must never break the caller
        status = getattr(e, "status_code", None) or getattr(e, "code", None)
        log.warning(
            "decision failed keys=%s error=%s status=%s latency_ms=%d",
            keys,
            type(e).__name__,
            status,
            int((time.perf_counter() - started) * 1000),
        )
        return None


def decide_yes_no(
    input_text: str,
    instructions: str,
    true_description: str | None = None,
    false_description: str | None = None,
    threshold: float = 0.8,
) -> Decision | None:
    """One yes/no question. `confident` = max(p_yes, 1-p_yes) >= threshold (0.5..0.99)."""
    q = YesNo(instructions, true_description, false_description, threshold)
    batch = decide(input_text, {"q": q})
    return batch["q"] if batch else None


def decide_choice(
    input_text: str,
    instructions: str,
    options: Mapping[str, str],
    min_confidence: float = 0.7,
) -> Decision | None:
    """One choice question over `options` (key -> criteria). Add a catch-all like "other"
    (not "none_of_the_above": the API sorts option keys alphabetically)."""
    batch = decide(input_text, {"q": Choice(instructions, options, min_confidence)})
    return batch["q"] if batch else None
