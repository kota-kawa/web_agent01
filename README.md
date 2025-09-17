# Web Automation Agent

This repository provides a typed domain specific language (DSL) and execution runtime for
controlling a Playwright driven browser.  The new pipeline validates and executes batches
of actions deterministically while emitting structured telemetry for post-run analysis.

## Typed DSL

All actions are defined as [Pydantic v2](https://docs.pydantic.dev/) models.  Each action
has a strict schema enforced by the shared registry located under `automation/dsl`.  The
minimum schema supported by the execution engine is illustrated below:

```json
{
  "run_id": "2025-09-17-001",
  "plan": [
    {"type": "navigate", "url": "https://example.com"},
    {"type": "wait", "for": {"state": "domcontentloaded"}},
    {"type": "type", "selector": {"role": "textbox", "aria_label": "Search"}, "text": "laptop", "press_enter": true},
    {"type": "wait", "for": {"selector": {"text": "Results"}, "state": "visible"}},
    {"type": "screenshot", "mode": "viewport"}
  ]
}
```

### Built-in actions

The registry currently exposes the following action types:

| Action      | Purpose                                                     |
|-------------|-------------------------------------------------------------|
| `navigate`  | Navigate the active tab to a URL.                            |
| `wait`      | Wait for document states, selectors or timeouts.             |
| `click`     | Click an element using the composite selector resolver.      |
| `type`      | Fill inputs with debounce and value verification.            |
| `select`    | Select options by value or label.                            |
| `press_key` | Dispatch key combinations (active element scope by default). |
| `scroll`    | Scroll the viewport or a specific element into view.         |
| `switch_tab`| Switch between browser tabs.                                 |
| `focus_iframe` | Push/pop iframe focus on the execution stack.             |
| `screenshot`| Capture viewport/fullpage/element screenshots.               |
| `extract`   | Extract text/value/href/html attributes.                     |
| `assert`    | Assert element visibility/attachment states.                 |

Additional legacy helpers remain available under `agent/actions/basic.py` for backward
compatibility.  They now produce payloads backed by the typed models.

### Composite selectors & stable IDs

Selectors are defined as structured objects accepting multiple strategies:

```json
{"css": "button.primary", "text": "Continue", "priority": ["css", "text"]}
```

The resolver (`vnc/selector_resolver.py`) scores candidates based on visibility,
clickability, viewport position, text similarity and proximity to reference text.
A stable node identifier is generated from the DOM path and text digest, allowing
retries to re-target the same node when possible.

## Deterministic execution pipeline

`vnc/executor.py` introduces a four phase pipeline:

1. **Plan** – Parse and normalise the DSL payload using the registry.
2. **Validate** – Perform structural checks (e.g. click actions must be followed by waits).
3. **Dry run** – Resolve selectors without side effects to surface errors early.
4. **Execute** – Perform actions with exponential backoff, jitter and selector re-resolution.

Each action produces an `ActionOutcome` of the form `{"ok": true/false, "details": {...}}`
which is returned to the caller and persisted in the run log.  Failures trigger an
`error_report.json` artifact with a snapshot of the failing action and warnings.

## Observability

Every step emits a JSONL event to `runs/{run_id}/events.jsonl` containing:

* `ts` – Unix timestamp.
* `run_id`, `step` – Identifiers.
* `action` – Normalised action payload.
* `resolved_selector` – Stable ID metadata when selectors are used.
* `result`, `warnings`, `error`, `retry_count`.
* `dom_digest_sha` – SHA256 of DOM path + text digest.
* `screenshot_path` – Saved screenshot (`runs/{run_id}/shots/step_XXXX.png`).

The `/events/<run_id>` endpoint serves the raw JSONL stream for external tooling.

## CLI

A lightweight CLI wrapper is available:

```bash
python -m agent.run --task examples/booking_task.json --server http://localhost:7000 --stream
```

* `--task` – Path to the DSL JSON file.
* `--server` – Automation server base URL (`http://localhost:7000` by default).
* `--headful` – Request headful execution (`config.headless = false`).
* `--stream` – Attempt to fetch the recorded events after completion.

## Configuration

Runtime configuration is loaded from environment variables (`AGENT_*`) and an optional
`config.toml` under the `[agent]` table.  The most important knobs are:

| Key                     | Description                    | Default |
|-------------------------|--------------------------------|---------|
| `action_timeout_ms`     | Per-action timeout             | 10000   |
| `navigation_timeout_ms` | Navigation timeout             | 30000   |
| `wait_timeout_ms`       | Wait/assert timeout            | 10000   |
| `max_retries`           | Retry attempts per action      | 3       |
| `log_root`              | Directory for run artifacts    | `runs`  |
| `headless`              | Launch browser headless        | `true`  |

## Tests

Run the test suite with:

```bash
pytest -q
```

The tests validate model normalisation, legacy helper compatibility and registry
behaviour.  Additional synthetic page fixtures can be added under `tests/` to exercise
browser-level integration.

## Example task

`examples/booking_task.json` demonstrates a minimal booking workflow using the typed DSL.

---

For more details on legacy endpoints and browser management consult `vnc/automation_server.py`.
