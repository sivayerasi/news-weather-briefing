# Mini-Project Spec: Daily News + Weather Briefing

> **Week 6** · **Owner:** Siva Yerasi
> **Build budget:** 1–2 days
> **Deliverable:** A deployed AWS agent that emails a daily briefing of weather and topic news tailored to the user's interests and city, with a live "preview now" page.

---

## 1. Goal & User Outcome

**Who uses it:** Someone who wants one short morning email with their local weather and news on topics they care about.

**What they get:** A daily briefing — current/forecast weather for their city plus a few news items filtered to their stated interests.

**How they trigger it:**
- [x] Web form (API Gateway HTTP API → Lambda) — set interests/city, "preview now" button *(Baseline front door)*
- [ ] File dropped in S3
- [x] Scheduled run (EventBridge cron — every morning)

**How they receive results:**
- [x] Rendered on the page (sync, the "preview now" button)
- [x] Outbound email via SES (the daily briefing)

---

## 2. Agent Design (Day 1)

| Part | This project |
|------|--------------|
| **Brain** (LLM + system prompt) | Fetches weather + news, filters to interests, writes a tight briefing |
| **Tools** (Hands) | `get_weather`, `get_news`, `filter_by_interest` |
| **Memory** (Notebook) | messages array + DynamoDB row for interests, city, email |
| **Skills** (Playbook) | Fetch weather → fetch news → filter to interests → format a short briefing |

**Single-agent or multi-agent?**
- [x] Single agent (one Lambda, agent loop)                *(Baseline shape)*
- [ ] Orchestrator / pipeline

---

## 3. Tools (Days 2–3)

| Tool name | What it does | Real API or mock? | Input schema (fields) |
|-----------|--------------|-------------------|-----------------------|
| `get_weather` | Current/forecast weather for the user's city | **Real** — Open-Meteo (Day 3) | `city: string` |
| `get_news` | Fetch recent headlines | Real free news API via httpx, or mock feed | `topics: array` |
| `filter_by_interest` | Keep only items matching the user's interests | Pure/LLM-assisted | `items: array`, `interests: array` |

> Weather is a real API (Open-Meteo). News can be a real free API or a stubbed feed depending on time.

---

## 4. Reliability & Guardrails (Day 4) — **Baseline, not optional**

- [x] **Max iterations** — bounded fetch/format passes.
- [x] **Error recovery** — if a source is down, skip it and note the gap; never fail the whole briefing over one dead source.
- [x] **Guardrails** — input: validate city/interests; output: no leaked secrets.
- [x] **Audit log** — sources queried, items kept, and final briefing logged to DynamoDB.

**Project-specific guardrail rule:** If a data source fails, the briefing must still send with a visible "weather/news unavailable today" note rather than erroring out.

---

## 5. AWS Architecture (Day 6) — **Baseline**

| Service | Used for | Baseline / Stretch |
|---------|----------|--------------------|
| **Lambda** | the briefing agent | Baseline |
| **Bedrock** (`converse()`) | calling Claude via IAM role | Baseline |
| **API Gateway (HTTP API)** | settings form + "preview now" | Baseline |
| **IAM** | Lambda role with Bedrock permissions | Baseline |
| **CDK** | one-command deploy | Baseline |
| **DynamoDB** | user settings + audit log | Baseline |
| **EventBridge** | daily morning schedule | Baseline |
| **SES** | emailed briefing | Baseline |
| **Step Functions** | — | Not used |
| **S3 (static site)** | host `index.html` | Stretch |

**Model ID:** `us.anthropic.claude-sonnet-4-6` · **Region:** `us-east-1`.

> **SES note:** sandbox mode — verify your own address for the demo.

---

## 6. Thin UI

**Form fields the user fills:** city, interests (comma-separated), email; a "preview now" button.

- [x] **Sync:** "preview now" returns today's briefing on the page.
- [ ] **Async:** not needed.

> Unauthenticated demo.

---

## 7. Exit Check

- [x] Agent loop works (weather + news combined into one briefing)
- [x] 3 tools with clear descriptions and schemas
- [x] All four Day-4 reliability patterns demonstrated
- [x] "Degrade gracefully on dead source" guardrail enforced
- [x] `cdk deploy` stands up the whole stack
- [x] Lambda calls Claude via Bedrock `converse()` (no API key in code)
- [x] "Preview now" returns a briefing; daily email arrives
- [x] README with architecture diagram + deploy steps
- [x] Code pushed to GitHub

---

## 8. Submission

- [x] GitHub repo (CDK + Lambda + `index.html`)
- [x] README as above
- [x] Screenshot/recording of a briefing (and one showing a skipped source)
- [x] Audit log from one real run

---

*Infinitra Studio · Week 6 · Mini-Project Spec*
