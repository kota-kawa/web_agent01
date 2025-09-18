# Web Automation Agent

## Overview
Web Automation Agent combines a strongly typed browser automation DSL, a hardened Playwright execution service, and a Flask-based controller that collaborates with LLM planners. Typed plans live under `automation/dsl`, the Playwright runtime is provided by the services in `vnc/`, and the conversational UI with LLM integration resides in `web/` and `agent/`.

## Project layout
- `automation/dsl/` – Pydantic v2 models for selectors and actions, the shared action registry, and utilities describing resolved DOM nodes.
- `vnc/` – Playwright server (`automation_server.py`), typed executor (`executor.py`), selector resolution (`selector_resolver.py`), safe interaction helpers, structured logging, and runtime configuration loaders.
- `agent/` – Legacy action shims, the async execution controller, browser API client, element catalog cache/formatters, LLM clients, memory helpers, and the CLI entry point.
- `web/` – Flask application, templates, and static assets that power the interactive controller.
- `examples/` – Ready-to-run typed plans (see `booking_task.json`).
- `tests/` – Pytest suite covering DSL validation, selector resolution, catalog handling, prompt generation, safe interactions, and DOM snapshot scripts.
- `docker-compose.yml` – Local development stack that boots both the automation service and the controller UI.

## Typed automation DSL
### Selectors and plans
`automation/dsl/models.py` defines `Selector`, a composite description that supports CSS, XPath, role, text, ARIA label, `index=N`, and stable identifier strategies with explicit priority ordering. Actions inherit from `ActionBase` and can serialise to typed or legacy payloads. `automation/dsl/registry.py` exposes an `ActionRegistry` plus `RunPlan`/`RunRequest` models that coerce arbitrary JSON into validated plans, making it possible to parse either `{"plan": [...]}` payloads or raw action lists.

### Built-in actions
The DSL ships with the following actions:

| Action | Purpose |
| --- | --- |
| `navigate` | Navigate to a URL and optionally wait for a state, selector, or timeout.
| `click` | Click selectors with configurable button, click count, and delay.
| `hover` | Hover over an element to reveal menus or tooltips.
| `type` | Fill inputs, optionally clearing first and pressing Enter.
| `select` | Choose options from `<select>` elements by value or label.
| `press_key` | Send key combinations scoped to the active element or entire page.
| `wait` | Pause for timeouts, document states, or selector visibility.
| `scroll` | Scroll by offset, to positions, or to target elements/containers.
| `scroll_to_text` | Scroll until the requested text snippet is visible.
| `switch_tab` | Activate pages by index, latest/previous/next, URL prefix, or title match.
| `focus_iframe` | Enter/exit frames using index, name, URL, element selector, parent, or root strategies.
| `refresh_catalog` | Request a rebuild of the element catalog when index mode is enabled.
| `eval_js` | Evaluate JavaScript in the page and capture the result.
| `click_blank_area` | Click outside elements to dismiss overlays.
| `close_popup` | Detect and close modal/pop-up overlays.
| `stop` | Pause execution and surface a stop-request to the controller.
| `screenshot` | Capture viewport, full page, or element screenshots with optional filenames.
| `extract` | Read text, value, href, or HTML from a selector.
| `assert` | Assert element visibility/hidden/attachment states.

`automation/dsl/models.py` also exposes `legacy_payload()` helpers so the same typed models can still emit the historical `{"action": ...}` schema that the controller expects.

## Selector resolution and legacy compatibility
`automation/dsl/resolution.py` provides lightweight data structures describing how a selector was resolved (DOM path, score, warnings). `vnc/selector_resolver.py` consumes those structures to score candidates across CSS/text/role/stable-id strategies, maintain a `StableNodeStore`, and reuse DOM handles between steps. For legacy payloads, `vnc/locator_utils.py` implements `SmartLocator`, a best-effort resolver that understands `data-testid`, `role=`, text, placeholders, and raw CSS selectors. `agent/actions/basic.py` bridges the typed models back to the legacy helpers used by prompts and older planners.

## Playwright automation service
`vnc/automation_server.py` exposes the HTTP API. It accepts typed plans (`{"plan": [...]}`) or legacy `{"actions": [...]}` payloads, performs JSON schema validation, enforces action limits, classifies errors, saves debug artifacts when enabled, and manages retries. When a typed plan is supplied it delegates to `RunExecutor` for deterministic execution; legacy payloads flow through the same safety wrappers while preserving backwards compatibility.

### Typed executor
`vnc/executor.py` drives typed runs. It loads configuration from `vnc/config.py`, prepares run directories, and initialises `StructuredLogger`/`LogPaths` from `vnc/structured_logging.py`. `ActionPerformer` performs a dry run to resolve selectors via `SelectorResolver`, executes each action with stabilisation from `vnc/page_stability.py`, and reuses hardened helpers in `vnc/safe_interactions.py` and `vnc/page_actions.py`. Retries apply exponential backoff, screenshots are written for every step, and failures generate `error_report.json` entries alongside structured JSONL logs.

### HTTP endpoints
Key endpoints include:
- `POST /execute-dsl` – Run typed or legacy payloads with correlation IDs and catalog awareness.
- `GET /source`, `/url`, `/screenshot` – Fetch current page HTML, URL, and screenshots.
- `GET /elements` – Return lightweight element summaries for debugging.
- `GET /catalog` – Serve the element catalog (abbreviated + full) when index mode is active.
- `GET /extracted` / `GET /eval_results` – Return data captured by `extract` and `eval_js` actions.
- `GET /stop-request` / `POST /stop-response` – Facilitate the stop/resume handshake.
- `GET /events/<run_id>` – Stream structured log events produced by the executor.
- `GET /healthz` – Liveness probe used by Docker and the controller.

### Logs and artifacts
Run data are stored under `runs/<run_id>/` (configurable) with per-step screenshots (`shots/step_XXXX.png`), chronological `events.jsonl` logs, and optional `error_report.json` files for failures.

## Element catalog and index mode
When `INDEX_MODE` is enabled the server maintains an element catalog with stable indices, caching it on disk and refreshing automatically when DOM signatures change. `vnc/automation_server.py` can regenerate catalogs, reconcile mismatched versions, and rebind index-based actions. On the client side `agent/element_catalog.py` caches responses from `/catalog`, tracks observed versions, and formats abbreviated catalog entries for prompt injection.

## Agent controller, UI, and LLM integration
`web/app.py` hosts the Flask UI, prepares prompts via `agent.controller.prompt`, dispatches plans asynchronously through `agent.controller.async_executor`, and talks to the Playwright service via `agent.browser.vnc` (HTML, DOM tree, element lists, DSL execution). Conversation history is persisted by `agent.utils.history` and surfaced through the simple memory interface in `agent.memory.simple`. LLM calls are routed through `agent.llm.client`, which supports Gemini and Groq models, optional screenshot attachments, and post-processing of JSON action plans. Front-end behaviour lives in `web/static/` while templates in `web/templates/` render the controller and run history.

## CLI and examples
Use `agent/run.py` to submit typed plans from the command line:

```bash
python -m agent.run --task examples/booking_task.json --server http://localhost:7000 --stream
```

The example plan demonstrates how to send a `RunRequest` directly to `/execute-dsl`.

## Running locally
`docker-compose.yml` builds two containers: the Playwright automation service (exposing noVNC, CDP, and the API) and the Flask controller/UI. Start the stack with:

```bash
docker-compose up --build
```

By default both services honour the `START_URL` environment variable so the initial navigation is predictable.

## Configuration
`vnc/config.py` reads defaults from `config.toml` and `AGENT_*` environment variables (timeouts, retry limits, headless/headful mode, log directory). `vnc/automation_server.py` respects additional environment toggles such as `ACTION_TIMEOUT`, `NAVIGATION_TIMEOUT`, `WAIT_FOR_SELECTOR_TIMEOUT`, `MAX_DSL_ACTIONS`, `INDEX_MODE`, domain allow/block lists, and `SAVE_DEBUG_ARTIFACTS`. The controller/UI honours `START_URL`, `MAX_STEPS`, and `LOG_DIR`, while the LLM client looks for `GEMINI_API_KEY`, `GEMINI_MODEL`, `GROQ_API_KEY`, and `GROQ_MODEL`.

## Testing
Run the test suite with:

```bash
pytest -q
```

The suite validates DSL parsing and legacy parity (`tests/test_dsl_models.py`), catalog scripts and rebind logic (`tests/test_catalog_collection_script.py`, `tests/test_catalog_rebind.py`, `tests/test_element_catalog.py`), selector utilities and resolution scoring (`tests/test_locator_utils.py`, `tests/test_selector_resolver.py`), prompt helpers (`tests/test_prompt.py`, `tests/test_prompt_improvements.py`), safe interaction fallbacks (`tests/test_safe_interactions.py`), and DOM snapshot utilities (`tests/test_dom_snapshot_script.py`).
