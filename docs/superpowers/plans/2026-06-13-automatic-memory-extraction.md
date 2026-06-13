# Automatic Memory Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent, silent, batched automatic memory extraction to private AstrBot conversations.

**Architecture:** Store user and final assistant messages in a SQLite buffer. A plugin-owned scheduler claims eligible per-user batches when either the message threshold or idle threshold is met, calls a configured/current AstrBot provider with a strict JSON extraction prompt, validates and applies candidates, then deletes successful batches or releases failed batches with retry backoff.

**Tech Stack:** Python 3.12+, AstrBot 4.25.5 hooks and `Context.llm_generate`, asyncio, aiosqlite, Quart Plugin Page APIs, vanilla HTML/CSS/JavaScript, pytest, Ruff.

---

## File Map

- Modify `astrbot_plugin_memory_cards/models.py`: note source fields and buffer/batch records.
- Modify `astrbot_plugin_memory_cards/store.py`: schema v2 migration, message buffering, batch claiming, success/failure, extraction status, source-aware notes.
- Create `astrbot_plugin_memory_cards/extraction.py`: strict prompt building, JSON parsing, credential filtering, candidate validation and deduplication helpers.
- Create `astrbot_plugin_memory_cards/scheduler.py`: trigger decisions and periodic extraction orchestration.
- Modify `astrbot_plugin_memory_cards/main.py`: message hooks, final assistant hook, scheduler lifecycle, provider selection, candidate application.
- Modify `astrbot_plugin_memory_cards/_conf_schema.json`: automatic extraction settings.
- Modify `astrbot_plugin_memory_cards/pages/memory/{index.html,app.js,style.css}`: source badge and extraction status.
- Modify `tests/astrbot_stubs.py`: final-agent hook and model-generation fakes.
- Create `tests/test_extraction.py` and `tests/test_scheduler.py`.
- Modify `tests/test_store.py`, `tests/test_hooks.py`, `tests/test_web_api.py`, `tests/test_webui_contract.py`, `tests/test_plugin_contract.py`.
- Modify `README.md`, `astrbot_plugin_memory_cards/README.md`, `CHANGELOG.md`, and `metadata.yaml`.

### Task 1: Schema v2 and persistent conversation buffer

**Files:**
- Modify: `astrbot_plugin_memory_cards/models.py`
- Modify: `astrbot_plugin_memory_cards/store.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Write failing migration and buffer tests**

Add tests that create a v1 database, reopen it with the new store, and assert old notes become `source="manual"`. Add tests for:

```python
await store.append_buffer_message(scope, "user", "我喜欢安静", "provider-a")
await store.append_buffer_message(scope, "assistant", "我记住了", "provider-a")
status = await store.get_extraction_status(scope)
assert status.pending_count == 2

batch = await store.claim_extraction_batch(
    scope,
    message_threshold=2,
    idle_before=None,
    now=now,
)
assert [item.role for item in batch.messages] == ["user", "assistant"]
```

Also assert a second claim returns `None`, success deletes only the claimed rows, failure releases them and sets `next_retry_at`, and messages arriving during processing remain pending for the next batch.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_store.py -v
```

Expected: failures for missing source fields, schema migration and buffer methods.

- [ ] **Step 3: Implement schema and records**

Set `SCHEMA_VERSION = 2`. Migrate v1 with:

```sql
ALTER TABLE notes ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE notes ADD COLUMN source_batch_id TEXT;
CREATE TABLE message_buffer (...);
CREATE TABLE extraction_state (...);
UPDATE schema_meta SET version = 2;
```

Add `source` and `source_batch_id` to `MemoryNote`; add immutable `BufferedMessage`, `ExtractionBatch`, and `ExtractionStatus` records. Implement append, status, eligible-user listing, atomic claim, complete, and fail methods under the existing lifecycle lock.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_store.py -v
```

Expected: all store tests pass.

- [ ] **Step 5: Commit**

```powershell
git add astrbot_plugin_memory_cards/models.py astrbot_plugin_memory_cards/store.py tests/test_store.py
git commit -m "feat: persist automatic extraction batches"
```

### Task 2: Strict extraction protocol

**Files:**
- Create: `astrbot_plugin_memory_cards/extraction.py`
- Create: `tests/test_extraction.py`

- [ ] **Step 1: Write failing parser and validation tests**

Cover:

```python
assert parse_candidates('{"memories": []}', max_notes=5) == []
assert parse_candidates("```json\n{\"memories\": []}\n```", max_notes=5) == []
```

Valid create/update candidates survive; invalid actions, categories, IDs, empty/oversized content, malformed JSON and extra candidates are rejected. Credential-like content such as passwords, OTPs, cookies, tokens and API keys is rejected. Prompt text includes both conversation roles and existing note IDs.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_extraction.py -v
```

Expected: module import failure.

- [ ] **Step 3: Implement pure extraction helpers**

Implement:

```python
def build_extraction_prompt(messages, existing_notes) -> str: ...
def parse_candidates(text: str, *, max_notes: int) -> list[MemoryCandidate]: ...
def normalize_for_duplicate_check(text: str) -> str: ...
def contains_credential_material(text: str) -> bool: ...
```

Use only standard-library JSON parsing. Strip one optional Markdown fence, require an object with a list named `memories`, whitelist actions/categories, and never execute model-provided operations directly.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_extraction.py -v
```

Expected: all extraction tests pass.

- [ ] **Step 5: Commit**

```powershell
git add astrbot_plugin_memory_cards/extraction.py tests/test_extraction.py
git commit -m "feat: validate automatic memory candidates"
```

### Task 3: Trigger decisions and scheduler lifecycle

**Files:**
- Create: `astrbot_plugin_memory_cards/scheduler.py`
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing trigger tests**

Use a fake clock/store/processor and assert:

- zero pending messages never schedule work;
- count `>= threshold` schedules immediately;
- idle age `>= idle_minutes` schedules;
- neither condition schedules nothing;
- retry time in the future suppresses work;
- one user cannot run two extraction jobs concurrently;
- stopping the scheduler cancels its loop and active jobs.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_scheduler.py -v
```

Expected: module import failure.

- [ ] **Step 3: Implement scheduler**

Implement an `ExtractionScheduler` with:

```python
async def start(self) -> None: ...
async def stop(self) -> None: ...
async def check_once(self) -> None: ...
```

Poll every 30 seconds, but only call the processor for scope keys returned by `store.list_extraction_statuses_with_pending()`. Keep an in-memory set of active scope keys and remove keys in `finally`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_scheduler.py -v
```

Expected: all scheduler tests pass.

- [ ] **Step 5: Commit**

```powershell
git add astrbot_plugin_memory_cards/scheduler.py tests/test_scheduler.py
git commit -m "feat: schedule batched memory extraction"
```

### Task 4: AstrBot hooks, provider selection and candidate application

**Files:**
- Modify: `astrbot_plugin_memory_cards/main.py`
- Modify: `tests/astrbot_stubs.py`
- Modify: `tests/test_hooks.py`
- Modify: `astrbot_plugin_memory_cards/_conf_schema.json`

- [ ] **Step 1: Write failing hook and processor tests**

Extend stubs with `filter.on_agent_done`, `Context.get_current_chat_provider_id`, and `Context.llm_generate`. Test:

- private user text is buffered after registration;
- groups, empty messages and missing identity are ignored;
- final assistant text is buffered once with current provider ID;
- configured provider overrides buffered provider;
- without either provider, the batch is released for retry;
- successful JSON creates `source="auto"` notes;
- exact normalized duplicates are skipped;
- valid same-scope update succeeds;
- cross-scope update is rejected;
- model/store failure does not affect the user reply.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_hooks.py -v
```

Expected: missing hooks/config/processor behavior.

- [ ] **Step 3: Implement plugin integration**

Keep the private-message observer and append the user text after `upsert_user`. Add:

```python
@filter.on_agent_done()
async def buffer_final_reply(self, event, run_context, resp) -> None: ...
```

Resolve provider with configured ID first, otherwise the batch's latest non-empty provider ID. Call:

```python
await self.context.llm_generate(
    chat_provider_id=provider_id,
    prompt=build_extraction_prompt(...),
    system_prompt=EXTRACTION_SYSTEM_PROMPT,
)
```

Apply candidates under scope checks, then complete or fail the batch. Start the scheduler after opening the store and stop it before closing the store.

Add bounded configuration fields with defaults: enabled `true`, idle `30`, threshold `20`, provider `""`, max notes `5`, retry `10`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_hooks.py tests/test_plugin_contract.py -v
```

Expected: all hook and contract tests pass.

- [ ] **Step 5: Commit**

```powershell
git add astrbot_plugin_memory_cards/main.py astrbot_plugin_memory_cards/_conf_schema.json tests/astrbot_stubs.py tests/test_hooks.py tests/test_plugin_contract.py
git commit -m "feat: extract memories from private conversations"
```

### Task 5: API and WebUI extraction visibility

**Files:**
- Modify: `astrbot_plugin_memory_cards/main.py`
- Modify: `astrbot_plugin_memory_cards/pages/memory/index.html`
- Modify: `astrbot_plugin_memory_cards/pages/memory/app.js`
- Modify: `astrbot_plugin_memory_cards/pages/memory/style.css`
- Modify: `tests/test_web_api.py`
- Modify: `tests/test_webui_contract.py`

- [ ] **Step 1: Write failing API and UI tests**

Assert users API includes `pending_message_count`, `last_message_at`, `last_extracted_at`, and `last_error`. Assert note JSON includes `source`. Assert frontend source contains “自动生成”, renders it via `textContent`, and displays pending/status information without `innerHTML`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_web_api.py tests/test_webui_contract.py -v
```

Expected: missing fields and UI markers.

- [ ] **Step 3: Implement status and badge UI**

Join extraction status into user summaries. Render a small `source-badge` only for `note.source === "auto"`. Show pending count and last extraction/error near the selected user. Preserve request-version race protection and Bridge-readable error envelopes.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_web_api.py tests/test_webui_contract.py -v
```

Expected: all API/UI contract tests pass.

- [ ] **Step 5: Commit**

```powershell
git add astrbot_plugin_memory_cards/main.py astrbot_plugin_memory_cards/pages/memory tests/test_web_api.py tests/test_webui_contract.py
git commit -m "feat: show automatic memory status"
```

### Task 6: Documentation, version and full verification

**Files:**
- Modify: `README.md`
- Modify: `astrbot_plugin_memory_cards/README.md`
- Modify: `astrbot_plugin_memory_cards/CHANGELOG.md`
- Modify: `astrbot_plugin_memory_cards/metadata.yaml`

- [ ] **Step 1: Update documentation and version**

Set plugin version to `v0.2.0`. Document automatic buffering, OR trigger semantics, default settings, silent behavior, provider selection, retry behavior, temporary retention of buffered private text, and administrator controls.

- [ ] **Step 2: Run full local verification**

Run:

```powershell
python -m pytest -q
python -m ruff check .
python -m compileall -q astrbot_plugin_memory_cards
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 3: Commit**

```powershell
git add README.md astrbot_plugin_memory_cards/README.md astrbot_plugin_memory_cards/CHANGELOG.md astrbot_plugin_memory_cards/metadata.yaml
git commit -m "docs: release automatic memory extraction"
```

### Task 7: Server migration, browser verification and GitHub publication

**Files:**
- Deploy: `astrbot_plugin_memory_cards/`
- Update after verification: `C:\Users\Lv_Tu\.codex\skills\building-astrbot-plugins\references\field-notes.md`

- [ ] **Step 1: Back up the server database**

Copy `/opt/1panel/apps/astrbot/astrbot/data/plugin_data/astrbot_plugin_memory_cards/memory.db` to a timestamped backup before deployment.

- [ ] **Step 2: Deploy and restart AstrBot**

Upload the plugin directory to `/opt/1panel/apps/astrbot/astrbot/data/plugins/astrbot_plugin_memory_cards`, restart the `astrbot` container, and confirm v0.2.0 loads without traceback.

- [ ] **Step 3: Verify migration and lifecycle**

Check schema version 2, preservation of existing note counts, scheduler startup, container restart persistence, and clean plugin reload/termination.

- [ ] **Step 4: Verify WebUI**

Open `http://127.0.0.1:16185/#/plugin-page/astrbot_plugin_memory_cards/memory`, confirm the existing layout, automatic badges/status, mobile responsiveness, no console errors, and no cross-user leakage.

- [ ] **Step 5: Verify a real extraction cycle**

Temporarily lower thresholds in plugin configuration, send a private conversation containing one safe long-term preference, wait for extraction, confirm one automatic note appears, then restore the intended defaults. Do not use credentials or sensitive content in the test.

- [ ] **Step 6: Record verified AstrBot lessons**

Append only genuinely new v4.25.5 findings about `on_agent_done`, provider resolution, scheduler shutdown or migration behavior to the field notes and validate the skill.

- [ ] **Step 7: Push GitHub**

```powershell
git push origin main
```

Confirm the remote main SHA matches local main and the README shows v0.2.0 behavior.
