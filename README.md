# Web Automation Agent

## Overview
This project combines a typed browser-automation DSL, a Playwright-based execution service, and a Flask control plane that cooperates with LLM planners to automate web browsing tasks deterministically. The typed models that describe automation plans live under `automation/dsl`, the Playwright runtime is exposed via the HTTP API in `vnc/automation_server.py`, and the interactive controller/UI is implemented in `web/app.py`.【F:automation/dsl/models.py†L1-L454】【F:vnc/automation_server.py†L1-L3082】【F:web/app.py†L1-L160】

### Repository layout
- `automation/dsl/`: Pydantic v2 models, selector utilities, and the action registry used to validate typed plans before execution.【F:automation/dsl/models.py†L1-L454】【F:automation/dsl/registry.py†L1-L186】
- `vnc/`: Playwright service exposing `/execute-dsl` alongside helper endpoints for HTML capture, screenshots, element catalogs, and structured run logs.【F:vnc/automation_server.py†L2647-L3082】
- `web/`: Flask application that orchestrates prompts, calls the automation service asynchronously, and normalises legacy selector formats for LLM output.【F:web/app.py†L1-L160】【F:tests/test_normalize_actions.py†L1-L57】
- `agent/`: Front-end helpers, legacy action shims, async execution manager, and browser client used by the controller.【F:agent/actions/basic.py†L1-L130】【F:agent/controller/async_executor.py†L1-L200】【F:agent/browser/vnc.py†L1-L200】
- `examples/`: Ready-made typed plans that can be sent to the executor (e.g. `examples/booking_task.json`).【F:examples/booking_task.json†L1-L14】
- `tests/`: Pytest suite covering model normalisation, selector conversion, and prompt helpers.【F:tests/test_dsl_models.py†L1-L53】【F:tests/test_normalize_actions.py†L1-L57】

## Typed DSL
Plans are defined as strongly typed Pydantic models. `Selector` supports composite strategies (CSS, role, text, ARIA label, etc.) with explicit priority ordering and legacy compatibility helpers.【F:automation/dsl/models.py†L17-L132】 Each action subclass extends `ActionBase`, providing both canonical `payload()` and `legacy_payload()` serialisations so that the controller can reuse the same definitions.【F:automation/dsl/models.py†L200-L262】

### Built-in actions
The registry currently includes the following typed actions, all implemented in `automation/dsl/models.py` and registered in `automation/dsl/registry.py`:

| Action | Purpose |
| --- | --- |
| `navigate` | Load a URL with optional follow-up wait conditions.【F:automation/dsl/models.py†L269-L279】 |
| `click` | Click a selector with configurable button/count/delay.【F:automation/dsl/models.py†L281-L293】 |
| `type` | Fill text inputs, optionally clearing first and pressing Enter.【F:automation/dsl/models.py†L295-L307】 |
| `select` | Choose options by value or label from `<select>` elements.【F:automation/dsl/models.py†L309-L318】 |
| `press_key` | Dispatch key combinations either to the focused element or the page.【F:automation/dsl/models.py†L321-L341】 |
| `wait` | Wait for timeouts, document states, or selector visibility.【F:automation/dsl/models.py†L343-L362】 |
| `scroll` | Scroll the viewport or a target element into view.【F:automation/dsl/models.py†L365-L379】 |
| `switch_tab` | Activate another page within the browser context (index, URL, title, etc.).【F:automation/dsl/models.py†L382-L390】 |
| `focus_iframe` | Push/pop iframe focus using index/name/url/selector strategies.【F:automation/dsl/models.py†L393-L401】 |
| `screenshot` | Capture viewport, full-page, or element screenshots.【F:automation/dsl/models.py†L404-L414】 |
| `extract` | Read text/value/href/html from a selector.【F:automation/dsl/models.py†L417-L426】 |
| `assert` | Assert element state (visible, hidden, attached, detached).【F:automation/dsl/models.py†L429-L438】 |

An example typed plan is provided in `examples/booking_task.json` and can be sent to `/execute-dsl` directly.【F:examples/booking_task.json†L1-L14】

## Legacy compatibility
Legacy controller helpers translate high-level calls (e.g. `basic.click("#submit")`) into the legacy action schema while still using the typed models internally.【F:agent/actions/basic.py†L1-L130】 The Playwright server continues to accept the historical `{"actions": [...]}` payload with the action types enumerated in `_ACTIONS`, validating inputs and coercing selectors before execution.【F:vnc/automation_server.py†L721-L835】 The Flask front-end also normalises structured selectors returned by LLMs into this legacy format to remain backwards compatible.【F:web/app.py†L103-L160】【F:tests/test_normalize_actions.py†L1-L57】

## Execution runtime
`RunExecutor` powers typed runs. When `/execute-dsl` receives a payload containing a `plan`, the server initialises the browser (optionally recreating an incognito context), builds a `RunRequest`, and hands it to `RunExecutor`. The executor:

1. Parses the payload into typed actions via the registry.【F:vnc/executor.py†L338-L390】
2. Validates click/wait sequencing and other structural constraints.【F:vnc/executor.py†L391-L397】
3. Performs a dry run to resolve selectors up front, surfacing locator failures before acting.【F:vnc/executor.py†L398-L405】
4. Executes each action with retries, exponential backoff, and specialised handlers for navigation, clicking, typing, scrolling, tab/iframe switching, screenshots, extraction, and assertions.【F:vnc/executor.py†L94-L335】【F:vnc/executor.py†L407-L434】

Every executed step is logged with a JSONL event, screenshot, selector metadata, and stable DOM digest, and failures also emit `error_report.json`.【F:vnc/executor.py†L436-L481】【F:vnc/structured_logging.py†L12-L75】 Run directories are created automatically under `runs/<run_id>/` (configurable).【F:vnc/config.py†L12-L84】

For legacy payloads, the Flask server still performs schema validation, catalog/version checks, and enhanced retries/error classification before replaying actions using Playwright helpers (safe click/fill/hover/select, popup closure, DOM stabilisation, etc.).【F:vnc/automation_server.py†L720-L3082】

## Element catalog and index mode
When `INDEX_MODE` is enabled, `/execute-dsl` maintains a structured element catalog that maps numeric indices to robust selectors, bounding boxes, and nearby text. The catalog is regenerated on demand or when DOM signatures change, enabling planners to refer to `index=N` targets reliably.【F:vnc/automation_server.py†L537-L666】【F:vnc/automation_server.py†L588-L666】 The controller can refresh or request catalog metadata through dedicated actions and HTTP endpoints.【F:agent/actions/basic.py†L124-L131】【F:vnc/automation_server.py†L2975-L3027】

## Observability and artifacts
Each run stores:
- `events.jsonl` – chronological action logs including resolved selector metadata, warnings, and screenshot paths.【F:vnc/structured_logging.py†L31-L75】
- `shots/step_XXXX.png` – per-step screenshots captured automatically.【F:vnc/executor.py†L436-L461】
- `error_report.json` – summary of failed actions (when retries exhaust).【F:vnc/executor.py†L463-L481】

The server exposes `/events/<run_id>` so tooling or the CLI can stream structured telemetry after completion.【F:vnc/automation_server.py†L3063-L3074】【F:agent/run.py†L53-L65】

## HTTP API quick reference
Key endpoints served by `vnc/automation_server.py`:
- `POST /execute-dsl` – Run typed (`plan`) or legacy (`actions`) payloads.【F:vnc/automation_server.py†L2647-L2921】
- `GET /source` – Current page HTML.【F:vnc/automation_server.py†L2924-L2934】
- `GET /url` – Current page URL.【F:vnc/automation_server.py†L2936-L2947】
- `GET /screenshot` – Base64 PNG screenshot.【F:vnc/automation_server.py†L2949-L2959】
- `GET /elements` – Lightweight DOM summary for debugging.【F:vnc/automation_server.py†L2962-L2972】
- `GET /catalog` – Element catalog (abbreviated + full entries) when index mode is enabled.【F:vnc/automation_server.py†L2975-L3027】
- `GET /extracted`, `GET /eval_results` – Data captured by `extract_text` and `eval_js` actions.【F:vnc/automation_server.py†L3030-L3037】
- `GET /stop-request` / `POST /stop-response` – Pause/resume handshake for stop actions.【F:vnc/automation_server.py†L3040-L3060】
- `GET /events/<run_id>` – Structured event log stream.【F:vnc/automation_server.py†L3063-L3074】
- `GET /healthz` – Simple health probe used by Docker and the controller.【F:vnc/automation_server.py†L3076-L3079】

## CLI usage
A lightweight CLI wrapper is provided in `agent/run.py`:

```bash
python -m agent.run --task examples/booking_task.json --server http://localhost:7000 --stream
```

Flags let you choose the server URL, override the log directory (`--out`), request headful execution, and stream events after completion.【F:agent/run.py†L21-L66】

## Running locally
A `docker-compose.yml` file starts both the Playwright service (with noVNC and CDP ports exposed) and the Flask controller/UI. The compose file mounts the repo into the containers so code changes are reflected immediately.【F:docker-compose.yml†L1-L39】 Launch the stack with:

```bash
docker-compose up --build
```

Once healthy, the automation API is available on `http://localhost:7000` and the web UI on `http://localhost:5000`.

## Configuration
Runtime defaults for the typed executor (timeouts, retries, log directory, headless/headful, screenshot mode) can be overridden via `AGENT_*` environment variables or a `[agent]` table in `config.toml` and are parsed by `vnc/config.py`.【F:vnc/config.py†L12-L76】 The automation server honours additional environment variables for navigation and locator timeouts, retry counts, SPA stabilisation, catalog behaviour, and domain allow/block lists (`ACTION_TIMEOUT`, `NAVIGATION_TIMEOUT`, `WAIT_FOR_SELECTOR_TIMEOUT`, `MAX_DSL_ACTIONS`, `INDEX_MODE`, `ALLOWED_DOMAINS`, `BLOCKED_DOMAINS`, etc.).【F:vnc/automation_server.py†L60-L107】

## Tests
Run the test suite with `pytest -q`. The tests cover registry parsing of legacy payloads, legacy payload emission, typed run validation, and selector/string normalisation used by the front-end controller.【F:tests/test_dsl_models.py†L1-L53】【F:tests/test_normalize_actions.py†L1-L57】 Additional integration tests can be added under `tests/` as needed.

## Additional resources
- Legacy action helpers (`agent/actions/basic.py`) remain available for prompt engineering while still benefiting from the typed models.【F:agent/actions/basic.py†L1-L130】
- The async executor (`agent/controller/async_executor.py`) manages background Playwright tasks and converts structured errors into user-facing warnings for the UI.【F:agent/controller/async_executor.py†L1-L200】
- Browser-facing helpers (`agent/browser/vnc.py`) provide retry logic, health checks, and warning normalisation when forwarding DSL payloads to the automation service.【F:agent/browser/vnc.py†L1-L200】
