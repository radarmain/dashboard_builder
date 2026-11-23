# LLM providers (design note)

Status: foundation only. `backend/app/llm/` is NOT wired into chat or the Tableau import yet.

## Provider order (planned)

1. **Bedrock (Claude) is primary** for the chat agent loop and Tableau import. Unchanged.
2. **Gemini is a fallback only** when Bedrock fails (throttling, 5xx, timeout, access error) **before any change has been applied** in that turn: no spec saved, no tool with side effects run. After the first applied change the turn finishes on Bedrock or fails; we never switch providers mid-turn.
3. A turn that used Gemini is **marked as fallback** (`provider: "gemini"`, `fallback: true`, model name) in the saved chat message and in logs, so analysts can see it and we can measure it.
4. Provider state is not portable: a Gemini turn's `provider_state` (thought signatures) is only replayed to Gemini. Conversation history crossing providers is sent as plain text/tool results.
5. Gemini capacity is not guaranteed (`gemini-3.8-flash` returned 503 "high demand" on every call during the 2026-11-23 session). Gemini failing too means the normal chat error path, no retry loops.

Code: `gemini_text.generate / tool_round_trip` (provider-agnostic `Message`, `ToolSpec`, `ToolCall`, `LLMResult`), typed `GeminiError` (`retryable`, `status`).

## API shape for tool calling: Interactions API, stateless

Use `client.interactions.create(model, input=<steps>, tools=[{"type": "function", ...}], store=False, system_instruction=..., generation_config={"thinking_level": ...})`.

- Google's current docs make Interactions the default and call `generateContent` legacy; the 3.8 Flash docs only show Interactions. New features land there first.
- Steps are explicit (`user_input`, `thought`, `function_call`, `function_result`, `model_output`), which maps cleanly onto our message/tool loop.
- `store=False` keeps history on our side (Google keeps no interaction). The model's own steps (thought signatures included) are kept as opaque `provider_state` on the assistant message and replayed unchanged. Not doing so can cause 400s on function calls.
- `usage.total_thought_tokens` is reported separately from output tokens.
- Gotchas: `thinking_level` is low/medium/high (`minimal` errors on 3.8; default is medium, so we pass low for routing-style work); no `temperature`/`top_p`; structured text right before a tool call can yield `Malformed_Function_Call` (use a dedicated `update` function for notes); keep the tool set small.

## Decisions API (calibrated routing, not generation)

`client.decisions.create(model="gemini-decision-1", input, questions)` returns typed yes/no and choice answers with calibrated probabilities and 0 output tokens, in about 0.3s. Wrapper: `backend/app/llm/decisions.py` (`decide`, `decide_yes_no`, `decide_choice`). Never raises (returns None; callers keep their current behavior). Yes/no probabilities are always 0.01..0.99, so yes/no thresholds live in 0.5..0.99 (confidence is `max(p, 1-p)`).

Planned uses (NOT implemented):
- **Chat message triage**: `new_chart | modify_existing | data_question | out_of_scope` plus "needs a clarifying question?". Use to pick a cheaper path or ask first; low confidence falls through to the normal agent.
- **Tableau worksheet triage**: "is this a chart/table we can rebuild", "is this a filter/parameter/utility sheet", from worksheet names, mark types and shelf structure only.
- **Duplicate / scratch detection**: are two worksheets or views the same thing; does a sheet look like a scratch/test/copy ("Sheet 3", "(copy)"), from names and structure only.

Decisions are advice for routing. They never authorize anything: SQL validation, table allowlist, and auth still decide. Treat input as untrusted (a message can argue for its own classification), and keep a second check on anything high-stakes (deleting or publishing is always out of scope for automation).

## What may be sent to Gemini

Allowed: chat message text, project/table/column names and types, descriptions, view titles, spec JSON (no data), Tableau worksheet names, mark types and shelf structure.

Never: credentials or tokens, the GBQ service account, Tableau PAT or session values, Tableau view data, anything from other clients, and **raw rows or query results**. The only exception is `RDB_GEMINI_ALLOW_ROW_SAMPLES=true` (default false); code that wants to include rows must check `gemini_text.row_samples_allowed()` first, and sample rows are for the current project only.

Logging: metadata only (question keys, chosen option key, confidence, latency, token counts, model, status). Never prompts, inputs, outputs or exception messages (API errors may echo the input). The key is a `SecretStr`.

## Config

Env names: `GEMINI_API_KEY`, `GEMINI_DEFAULT_MODEL` (gemini-3.8-flash), `GEMINI_DECISION_MODEL` (gemini-decision-1). Switches: `RDB_GEMINI_ENABLED` (true), `RDB_DECISIONS_ENABLED` (true), `RDB_DECISIONS_TIMEOUT_SECONDS` (8), `RDB_GEMINI_ALLOW_ROW_SAMPLES` (false), `RDB_GEMINI_TEXT_TIMEOUT_SECONDS` (60). Deployed: `RDB_GEMINI_SECRET_NAME=rdb_gemini` (JSON keys `api_key`, `default_model`, `decision_model`) replaces the env values; created by `scripts/create_gemini_secret.py`, readable only by `rdb-api-role`.

SDK note: the google-genai default retry loop (5 attempts with backoff) turns a 503 into a 30s+ hang, so `gemini_client` sets explicit timeouts and `attempts=1`.
