# Modules Guide

A short guide to every file in the project and what it does. For setup, deployment and screenshots, see `README.md`.

## How a request flows

```
Browser / daily alarm
   -> handler.py        receives the request and decides what to do
   -> guardrails.py     checks the input
   -> agent.py          asks the AI, in a loop
   -> tools.py          weather, news and filter helpers the AI can ask for
   -> store.py          saves settings and the audit log in DynamoDB
   -> handler.py        sends the reply, or emails the briefing
```

## Lambda code (`lambda/briefing/`)

| File | Job | Key functions |
|---|---|---|
| `handler.py` | Front desk. Receives every request and sends it to the right job. Also sends the email. | `lambda_handler`, `handle_preview`, `handle_settings`, `handle_daily`, `send_email`, `to_html` |
| `agent.py` | The agent loop. Sends the request and the tool menu to the AI, runs the tools it asks for, repeats up to 6 rounds, then cleans the final text. | `run_agent`, `_enforce_gap_notes`, `_clean` |
| `tools.py` | The three tools the AI can use, plus the menu that describes them. Each tool returns "unavailable" instead of crashing. | `get_weather`, `get_news`, `filter_by_interest`, `run_tool`, `TOOL_SPECS` |
| `guardrails.py` | Checks input (city, interests, email) and hides anything that looks like a secret in the output. | `validate_request`, `parse_interests`, `scrub_output` |
| `store.py` | Talks to DynamoDB: saves settings, lists saved users, writes one audit row per run (deleted after 90 days). | `save_settings`, `list_settings`, `write_audit` |
| `index.html` | The web page. Plain HTML, CSS and JavaScript. Sends the form to the API and shows the briefing and the "Run details" box. | none (JavaScript in the page) |

### `handler.py` in more detail

| Function | What it does |
|---|---|
| `lambda_handler` | Entry point. Daily alarm goes to `handle_daily`. `GET /` returns the page. `POST /preview` and `POST /settings` go to their handlers. Bad input returns 400, unexpected errors return 500, unknown paths return 404. |
| `handle_preview` | Validates input, runs the agent, writes the audit row, returns the briefing and run details. |
| `handle_settings` | Validates input (email required), saves the settings. |
| `handle_daily` | For every saved user: make the briefing, email it, write the audit row. One failed user does not stop the others. |
| `run_for` | Calls the agent with a user's city and interests. |
| `send_email` | Sends through SES as HTML with a plain-text fallback. Returns `sent`, `skipped` or `failed`. |
| `to_html` | Turns the plain-text briefing into a neat HTML email. |
| `_safe_audit` | Writes the audit row, but a logging failure never stops the briefing. |
| `_response` | Builds the reply in the shape API Gateway expects. |
| `bedrock`, `ses` | Create the AI and email connections once and reuse them. |

### `tools.py` in more detail

| Function | What it does |
|---|---|
| `get_weather` | Finds the city's coordinates, then gets current weather and a 3-day forecast from Open-Meteo. |
| `_geocode` | Turns a town name into coordinates and a country code. Open-Meteo first, OpenStreetMap as a backup. |
| `get_news` | For each interest, gets up to 3 headlines from Google News RSS, removes repeats, sorts newest first. |
| `_fetch_topic` | News for one topic: tries your country's edition, then the US one, and retries once if empty. |
| `_parse_rss` | Reads the news feed and returns clean headline records tagged with their topic. |
| `filter_by_interest` | Keeps headlines that match an interest. |
| `run_tool` | Receives the AI's request and runs the matching function. |
| `TOOL_SPECS` | The menu that tells the AI which tools exist and what inputs they need. |

## Infrastructure and settings

| File | Job |
|---|---|
| `app.py` | CDK entry point. Creates the stack named `news-weather-briefing`. |
| `infra/stack.py` | Describes every AWS resource: Lambda, API Gateway, DynamoDB table, EventBridge daily rule and the IAM permissions. |
| `cdk.json` | Settings: the daily send time (UTC). The sender email is passed at deploy time. |

## Tests and docs

| Path | Job |
|---|---|
| `tests/test_briefing.py` | 40 unit tests. They run without AWS, using a fake AI and fake web responses. |
| `pytest.ini` | Tells pytest where to find the Lambda code. |
| `docs/` | Project spec, sample audit log, screenshots and diagrams. |

## The four reliability patterns

| Pattern | Where |
|---|---|
| Max iterations (6 rounds) | `agent.py` |
| Error recovery ("unavailable" instead of crashing) | `tools.py`, `agent.py`, `handler.py` |
| Guardrails (input checks, secret scrubbing) | `guardrails.py` |
| Audit log | `store.py` |
