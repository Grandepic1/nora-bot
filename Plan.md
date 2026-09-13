# Plan: Migrate NORA from Gemini to Ollama (local-model branch)

## Goal

Replace the Google Gemini SDK (`google-genai`) with a locally hosted Ollama
backend, fully (no Gemini fallback), with a full rename of Gemini-specific
naming. Google Sheets integration stays exactly as it is
(`google-api-python-client` remains).

## Decisions (confirmed)

| Decision | Choice |
| --- | --- |
| Ollama client | Official `ollama` Python package (`AsyncClient`) |
| Naming | Full rename: `gemini.py` → `ollama.py`, `GeminiService` → `OllamaService`, `bot.gemini` → `bot.ollama`, `gemini.*` log events → `ollama.*` |
| Fallback | None — full replacement, delete `google-genai` |

## Current state (what we learned)

- `app/services/gemini.py` owns everything: per-session `ChatState`, message
  debounce (2s), queueing, inactivity timer (5 min) and shutdown. It calls
  `client.aio.chats.create(model, config={system_instruction, tools})` and
  `chat.send_message()`.
- **Critical detail:** the `google-genai` SDK runs the tool-calling loop
  internally — tools from `app/tools/sheets.py` are plain async Python
  functions with docstrings. With Ollama we must implement the loop ourselves.
- Tool surface (`build_sheet_tools`): reads `list_sheets`, `read_sheet`,
  `read_row`; writes `append_rows`, `update_row`, `create_sheet`,
  `rename_sheet` (return `pending_confirmation` dicts); managers
  `confirm/preview/cancel_sheet_action`. The write-confirm flow
  (`PendingSheetActionService`, preview web UI, tokens) is provider-agnostic
  and must not change.
- `ActionOrigin` propagation uses a `ContextVar`
  (`current_action_origin`) set around `send_message()` — must be set around
  our own chat loop instead. Since tool execution moves into our loop, this
  keeps working.
- Wiring points: `app/waha_handler/bot.py` (`_gemini_context`, `bot.gemini`),
  `app/commands/ai.py` (`bot.gemini.queue_message`), and
  `app/commands/spreadsheet_commands.py` (`bot.gemini.remove_chat` ×3).
- Env: `GEMINI_KEY`, `GEMINI_MODEL` in `.env` / `.env.example`.
- Tests: `tests/test_gemini_inactivity.py` builds the service via
  `object.__new__(GeminiService)` and monkeypatches `_create_chat`; also stubs
  nothing else. `tests/test_context.py`, `test_debug_logging.py`,
  `test_sheet_confirmation.py` do not touch Gemini.

## Migration design

### 1. Dependencies & config

- `pyproject.toml`: remove `google-genai>=2.22.0`, add `ollama>=0.4.0`
  (then `uv lock` / `uv sync` to update `uv.lock`).
- `.env` / `.env.example`:
  - Remove `GEMINI_KEY`, `GEMINI_MODEL`.
  - Add `OLLAMA_HOST` (default `http://127.0.0.1:11434`) and `OLLAMA_MODEL`
    (required, e.g. a tool-capable model such as `llama3.1` or `qwen2.5`).
  - `OLLAMA_KEEP_ALIVE` (optional, seconds; default e.g. `600`) so the model
    stays warm between WhatsApp messages.

### 2. `app/services/ollama.py` (replaces `gemini.py`)

Keep everything that is provider-agnostic as-is (queue, debounce, inactivity,
shutdown, `remove_chat`, dataclasses). Changes:

- **Client init:** `self.client = ollama.AsyncClient(host=OLLAMA_HOST)`;
  `self.model = os.environ["OLLAMA_MODEL"]`. Raise `RuntimeError` if
  `OLLAMA_MODEL` is unset (mirror the old `GEMINI_KEY` check). Accept an
  optional injected `client` constructor param for tests.
- **History instead of SDK chat:** replace `ChatState.chat` /
  `chat_spreadsheet_id` with `history: list[dict]` +
  `history_spreadsheet_id: str | None`. Recreate history when
  `spreadsheet_id` changes (same trigger as today's `_create_chat` re-check).
- **Tool schemas:** keep `build_sheet_tools` returning Python async
  callables; the `ollama` package serializes callables (type hints +
  docstrings) into tool JSON schemas automatically. Keep a
  `name → callable` map alongside for dispatch. No changes needed in
  `app/tools/sheets.py` besides verification.
- **Manual tool loop** (the core new logic, replacing
  `chat.send_message`):
  1. If history is empty, seed it with the system message
     (`SYSTEM_INSTRUCTION`, kept verbatim).
  2. Append the user message.
  3. `response = await client.chat(model=..., messages=history, tools=tools)`.
  4. While `response.message.tool_calls`:
     - Append `response.message` to history.
     - For each tool call: resolve in the map; execute with
       `arguments` (defensively parse if the server returns a JSON string);
       wrap execution in try/except so a tool error becomes a `role=tool`
       message the model can react to instead of crashing the turn.
     - Append each result as `{"role": "tool", "content": json.dumps(result,
       default=str)}`.
     - Call `client.chat` again (same `tools`).
  5. Append the final assistant message to history; return its text
     (fallback text unchanged: `"NORA tidak dapat menghasilkan respons."`).
  6. Set the `current_action_origin` ContextVar around the whole loop
     (one token set/reset, like today's `send_message` wrapper).
- **History cap:** trim to the system message + last N (≈40) messages when
  appending, since local context windows are small. Configurable via
  `OLLAMA_MAX_HISTORY_MESSAGES` (optional).
- **Logging:** rename all `gemini.*` debug events to `ollama.*`
  (`ollama.chat.created`, `ollama.message.queued`, etc.).
- **Shutdown:** drop `client.aio.aclose()`; close the AsyncClient if the
  installed version exposes a close (verify during implementation; ollama
  versions differ — worst case the httpx client is GC'd).

### 3. Wiring renames

- `app/waha_handler/bot.py`: `from app.services.ollama import OllamaService`;
  attribute `self.gemini` → `self.ollama`; cleanup context
  `_gemini_context` → `_ollama_context`; `gemini.lifecycle.*` log events →
  `ollama.lifecycle.*`.
- `app/commands/ai.py`: `bot.gemini.queue_message(...)` →
  `bot.ollama.queue_message(...)`.
- `app/commands/spreadsheet_commands.py`: three `bot.gemini.remove_chat(...)`
  → `bot.ollama.remove_chat(...)`.

### 4. Tests

- Rename `tests/test_gemini_inactivity.py` → `tests/test_ollama_inactivity.py`;
  `GeminiService` → `OllamaService`. Keep the seam: instead of
  monkeypatching `_create_chat`, inject a fake `AsyncClient` via the new
  constructor param; the fake returns canned `chat()` responses so the
  inactivity/debounce tests stay focused and framework-free (existing style).
- Add one focused unit test for the tool loop: fake client returns a
  `tool_calls` response, then a text response; assert the tool callable
  executed with parsed arguments, `role=tool` result messages were appended,
  and the final text was returned.
- Update `test_system_instruction_targets_whatsapp_output` import path.
- Run the full suite: `uv run python -m unittest discover tests`.

## Implementation order

1. `pyproject.toml` deps + `uv lock` / `uv sync`.
2. Create `app/services/ollama.py` (port + new tool loop).
3. Rewire `bot.py`, `ai.py`, `spreadsheet_commands.py`.
4. Delete `app/services/gemini.py`; drop `google-genai`.
5. Update `.env.example` (+ local `.env`), create `.env.example` entries for
   Ollama.
6. Rename/update tests; add tool-loop test.
7. Typecheck / run tests; fix fallout.
8. Manual smoke test against a real Ollama instance: `/start`,
   plain chat, read tool, write tool → pending confirmation → confirm.

## Risks / notes

- **Model quality:** local models vary in tool-calling reliability; pick a
  tool-capable model (llama3.1+, qwen2.5+). The strict
  prepare→confirm→act sheet flow protects against malformed writes.
- **Latency:** first token on consumer hardware is slow; typing indicator +
  existing debounce hide some of it. `OLLAMA_KEEP_ALIVE` avoids reloads.
- **Schema fidelity:** ollama's callable→schema serialization uses type hints;
  `list[list]`-style hints are loose — verify tool args arrive sane, tighten
  hints if needed.
- **Arguments as JSON string:** some Ollama versions return
  `tool_calls[].function.arguments` as a string — parse defensively.
- Sheets service account, preview UI, and all DB models are untouched.

## Out of scope

- Streaming responses, multi-provider abstraction, Gemini fallback.
