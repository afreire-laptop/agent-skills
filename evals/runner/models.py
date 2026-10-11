"""Frozen data models, and the names of the conditions, harnesses and dimensions they use."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

SKILL_NAME = "technical-writing"
CONDITIONS = {"A": "no guidance", "B": "short rule", "C": "skill"}
HARNESSES = ("claude", "codex")
Dimension = Literal["clarity", "naturalness", "brevity", "conventions"]
DIMENSIONS: tuple[Dimension, ...] = get_args(Dimension)


class Model(BaseModel):
    """Immutable, strictly typed data. Unknown keys are errors."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class External(Model):
    """Data that a CLI or a model wrote. Unknown keys are ignored."""

    model_config = ConfigDict(frozen=True, strict=True, extra="ignore")


M = TypeVar("M", bound=BaseModel)


class CheckBase(Model):
    id: str
    target: Literal["output", "message", "header"] = "output"
    flags: Annotated[str, Field(pattern=r"^[ims]*$")] = ""
    ignore_quoted: bool = False


class PhraseCheck(CheckBase):
    type: Literal["contains", "not_contains"]
    value: str


class PatternCheck(CheckBase):
    type: Literal["regex", "not_regex"]
    pattern: str


class RatioCheck(CheckBase):
    type: Literal["max_word_ratio"]
    value: float


Check = Annotated[PhraseCheck | PatternCheck | RatioCheck, Field(discriminator="type")]


class Case(Model):
    id: int
    name: str
    artifact: str | None = None
    prompt: str
    files: Sequence[str]
    expected_output: str | None = None
    expectations: Sequence[str]
    checks: Sequence[Check]


class CaseFile(Model):
    skill_name: str | None = None
    evals: Sequence[Case]


class TriggerQuery(Model):
    query: str
    should_trigger: bool
    note: str | None = None


TRIGGER_QUERIES = TypeAdapter(tuple[TriggerQuery, ...])


class Guidance(Model):
    """The writing guidance conditions B and C add to the task."""

    short_rule: str
    skill_body: str | None


class HarnessOptions(Model):
    claude_model: str | None = None
    claude_effort: str | None = None
    codex_model: str | None = None
    codex_effort: str | None = None
    timeout: int = 600

    def with_effort(self, effort: str | None) -> HarnessOptions:
        return self.model_copy(update={"claude_effort": effort, "codex_effort": effort}) if effort else self


Usage = Mapping[str, int | float | None]


class CallResult(Model):
    ok: bool
    text: str = ""
    structured: object = None
    usage: Usage = Field(default_factory=dict)
    model: str | None = None
    duration_s: float = 0.0
    error: str | None = None


class ClaudeUsage(External):
    input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    output_tokens: int | None = None


class ClaudeReply(External):
    """`claude -p --output-format json`."""

    is_error: bool | None = None
    result: str | None = None
    structured_output: object = None
    usage: ClaudeUsage | None = None
    model_usage: Mapping[str, object] | None = Field(default=None, alias="modelUsage")
    total_cost_usd: int | float | None = None


class CodexUsage(External):
    input_tokens: int | None = None
    output_tokens: int | None = None


class CodexEvent(External):
    """One line of `codex exec --json`."""

    type: str | None = None
    usage: CodexUsage | None = None
    item: Mapping[str, object] | None = None


class ContentBlock(External):
    type: str | None = None
    name: str | None = None
    input: object = Field(default_factory=dict)


class ClaudeMessage(External):
    content: Sequence[ContentBlock] = ()


class ClaudeEvent(External):
    """One line of `claude -p --output-format stream-json`."""

    type: str | None = None
    subtype: str | None = None
    skills: Sequence[object] | None = None
    message: ClaudeMessage | None = None


class ExpectationGrade(External):
    index: int
    verdict: Literal["pass", "fail", "unknown"]
    evidence: str


class ResponseGrade(External):
    label: str
    analysis: str
    expectations: Sequence[ExpectationGrade]
    invented_facts: Sequence[str]
    omitted_facts: Sequence[str]
    fidelity: Literal["pass", "fail"]


class Rank(External):
    label: str
    rank: int


class DimensionRanks(External):
    dimension: Dimension
    analysis: str
    ranks: Sequence[Rank]


class JudgeResult(External):
    """A judge's structured output. Mirrors judge_schema(); selftest checks that they agree."""

    responses: Sequence[ResponseGrade]
    dimensions: Sequence[DimensionRanks]


class TriggerOutcome(Model):
    triggered: bool
    ended: str
    discovered: bool | None


class GenerationMeta(External):
    """gen/<harness>/<case>/<condition>/t<n>/meta.json."""

    ok: bool
    harness: str
    case: str
    condition: str
    trial: int
    prompt_sha: str | None = None
    model: str | None = None
    usage: Usage = Field(default_factory=dict)
    duration_s: float | None = None
    output_words: int
    error: str | None = None


class CheckRow(External):
    """One entry of checks.json."""

    harness: str
    case: str
    condition: str
    trial: int
    check: str
    passed: bool
    detail: str


CHECK_ROWS = TypeAdapter(tuple[CheckRow, ...])


class JudgedConditions(External):
    """The part of a stored judgement that says which conditions it compared."""

    mapping: Mapping[str, str]


class StoredJudgement(JudgedConditions):
    """judge/<harness>/<case>/t<n>/<judge>.json."""

    judge: str
    judge_model: str | None = None
    usage: Usage = Field(default_factory=dict)
    result: JudgeResult


class Judged(Model):
    """A stored judgement and the group its path names."""

    harness: str
    case: str
    trial: str
    judge: str  # from the file name
    judgement: StoredJudgement


class TriggerRun(External):
    """triggers/<harness>/q<nn>-r<n>.json."""

    triggered: bool
    ended: str
    discovered: bool | None
    harness: str
    index: int
    attempt: int
    query: str
    should_trigger: bool
    duration_s: float | None = None


class Invocation(Model):
    at: str
    argv: Sequence[str]
    git_commit: str | None
    git_dirty: bool
    skill_sha: str | None
    short_rule_sha: str
    cases_sha: str
    claude_version: str | None
    codex_version: str | None
    options: Mapping[str, object]


class Manifest(External):
    """manifest.json. Earlier invocations are kept as they were written."""

    created: str
    invocations: Sequence[Mapping[str, object]] = ()


class ReportParams(Model):
    """What a run was asked to produce, from the command line."""

    run_id: str
    harnesses: tuple[str, ...]
    conditions: tuple[str, ...]
    trials: int
    judge_harness: str
    trigger_runs: int


class ConditionStats(Model):
    check_pass_rate: float | None
    check_pass_sd: float | None
    expectation_pass_rate: float | None
    expectation_unknown: int
    fidelity_pass_rate: float | None
    sensitive_leaks: int
    mean_input_tokens: float | None
    mean_output_words: float | None


class GroupCounts(Model):
    first: int
    tie: int
    second: int


class PairStats(Model):
    groups: GroupCounts
    cases_preferred: tuple[str, ...]
    net_by_case: Mapping[str, int]
    mean_case_preference: float
    ci95: tuple[float, float]


class TriggerStats(Model):
    recall: float | None
    false_positive_rate: float | None
    runs: int
    undiscovered_runs: int


class HarnessStats(Model):
    """Everything the gates read for one writer, and what report.json records."""

    conditions: Mapping[str, ConditionStats]
    overhead: Mapping[str, float] = Field(default_factory=dict)  # input tokens over A, per condition
    pairs: Mapping[str, PairStats] = Field(default_factory=dict)  # "C_vs_B" and so on
    triggers: TriggerStats | None = None


def parsed(model: type[M], raw: object) -> M | None:
    """`raw` as a `model`, or None if it does not validate."""
    try:
        return model.model_validate(raw)
    except ValidationError:
        return None


def first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    return f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
