# trySearch — 2-week sprint to a demo-ready product

Written 26 Aug 2026. Companion to `CLAUDE.md` (the rules) and the project docs
`docs/architecture-spec.md` (the DDL and contracts) and
`docs/prototype-audit.md` (why each of these is here).

**Goal at the end of day 10:** a multi-tenant product tracking real prompts across 3 engines, with a
correct Visibility Score carrying confidence intervals, a generated starter prompt set, a citation
view, and a white-label PDF report you can send to a prospect.

**Not in these two weeks:** alerts, GA4, sentiment, Stripe, agent chat, crawler log analytics,
scraper vendors. None of those are why someone buys. Week 3+.

**Database is empty.** No data migration anywhere in this plan. Drop legacy tables freely.

---

## Day 0 — your 90 minutes. Nothing below starts without this.

| # | Do this | Time | Blocks |
|---|---|---|---|
| 1 | Google AI Studio → create a Gemini API key | 5 min | T13. Free tier gives 5,000 grounded prompts/month **per Google Cloud project** |
| 2 | OpenAI platform → API key, add $5 credit | 10 min | T13 |
| 3 | Anthropic console → API key | 10 min | T13 + extraction |
| 4 | Confirm the Perplexity key still works | 5 min | T12 |
| 5 | Google Cloud Console → OAuth 2.0 client, add the Search Console + Analytics scopes, set the redirect URI | 40 min | Week 3 GA4, and switches on the GSC module you already built |
| 6 | Render → provision a second Postgres as staging; put its URL in the agent's env, never prod's | 10 min | everything |

Put every key in Render env vars and in a local `.env`. Never let a key reach the repo.

---

## Getting started

Commit these files at the repo root, then open Claude Code in the repo and start with:

> Read CLAUDE.md and SPRINT.md. Do T1 only — split the monolith and delete the Mongo branch.
> Also untrack searchable.db and .DS_Store. Open a PR when the acceptance criteria pass.

Then one task per session, in order. Do not let it run ahead — **T10 is worthless if T9 is wrong.**

Files in place after unzipping:

```
CLAUDE.md                      # Claude Code reads this automatically every session
SPRINT.md                      # this file — point it here, task by task
docs/architecture-spec.md      # DDL, adapter contract, job rules, extraction pipeline
docs/prototype-audit.md        # what already exists and why each task is here
docs/searchable-teardown.md    # how the competitor works, feature by feature
docs/PRD.md                    # acceptance criteria, Visibility Score spec, cost model
docs/handover-plan.md          # yours only — account migration notes, not for the agent
```

## How to run this with full delegation

One task per branch. One PR per task. **Review means reading the test output and the PR
description, not the diff** — that only works because every task below has a machine-checkable
acceptance criterion, so build T1–T4 first even though they ship nothing visible.

If a task's tests don't pass, don't merge and don't move on. The plan compounds: T10 is meaningless
if T9 is wrong.

---

# Week 1 — foundation

Nothing here is client-visible. All of it becomes impossible to retrofit once there's real data.

### T1 · Split the monolith · ~4h
`server_pg.py` (4,756 lines) becomes the module tree in `CLAUDE.md`. Pure move — no behaviour
changes, no renames beyond the file boundary. Delete `server.py`, `server_mongo.py`,
`mongo_config.py`, `migrate_postgres_to_mongo.py`, and drop `pymongo` + `dnspython` from
`requirements.txt`.

**Accept:** every existing route responds identically before and after (snapshot test hitting all
56 routes) · `pytest` green · `grep -ri mongo` returns nothing outside `SPRINT.md` · app boots on
Render.

### T2 · Alembic · ~2h
Introduce Alembic. Baseline migration captures the current schema. Delete
`ensure_database_column` and any `create_all` in app code.

**Accept:** `alembic upgrade head` builds the schema from empty · `alembic downgrade base` tears it
down · no schema DDL left outside `migrations/`.

### T3 · Fixture harness · ~3h
`scripts/record_fixture.py` makes one real call to a provider with a real key and writes the raw
JSON to `tests/fixtures/<engine>/<case>.json`. Add a `pytest` fixture that replays them. Record one
Perplexity search + one Perplexity agent response now.

**Accept:** `pytest` passes with no network access at all (run it with networking disabled to
prove it) · at least two recorded Perplexity fixtures committed.

### T4 · CLAUDE.md + CI · ~1h
Commit `CLAUDE.md`. GitHub Actions running `pytest` on every PR, blocking merge on red.

**Accept:** a deliberately failing test blocks a PR.

### T5 · Tenancy schema — clean slate · ~6h
Create `organizations`, `memberships`, `workspaces`, `brand_aliases`, `competitors` per the
architecture spec §2.1. Add `workspace_id` to every workspace-scoped table and drop `user_id` from
them. **Drop outright:** `analytics_runs`, `analytics_engine_metrics`, `analytics_prompts`,
`prompt_collections`, `prompt_queries`, `prompt_query_results`, `visibility_watchlists`,
`visibility_scans`, `visibility_engine_results`, `visibility_mentions`, `master_workspaces`.
`analytics_projects` becomes `workspaces`.

**Accept:** `alembic upgrade head` from empty produces the new schema · table count is ~26, not 36 ·
`is_legacy_mock_analytics_run` is gone along with the tables that needed it.

### T6 · One isolation guard · ~5h
`app/tenancy.py` exposes `require_workspace(workspace_id)` and `require_org(org_id)`, resolving the
current user's membership and role. Delete `project_for_user`, `watchlist_for_user`,
`prompt_collection_for_user`, `content_document_for_user` and every hand-written
`WHERE user_id = ?`. Every workspace-scoped route goes through the guard.

**Accept:** an isolation test creates two orgs with one workspace each and asserts **every**
workspace-scoped route returns 404 for the wrong org · `grep -r "user_id ==" app/routes/` returns
nothing · role checks reject `client_viewer` on writes.

### T7 · Jobs off the web process · ~4h
Delete `start_background_analytics_job` and `threading.Thread`. All execution moves to the CLI
worker. Keep the 45-minute stale-lease recovery — it's correct. Replace the 25-prompt `409` with
batching: a 300-prompt workspace becomes 12 sequential batches. Fan-out parents never retry;
individual answers retry 3× with jittered backoff and mark the run `partial`.

**Accept:** an on-demand scan returns immediately with a job id and completes in the worker · a
300-prompt workspace scans without a 409 · killing the worker mid-run leaves the job recoverable,
not lost · a forced single-answer failure produces `status='partial'`, not `failed`.

### T8 · usage_ledger and ceilings · ~4h
Table per architecture spec §2.4, with `org_id` denormalised. One row per provider call including
failures, with an estimated cost. Ceiling checked **before** dispatch against this month's rows for
the org. Alert at 60%, refuse at 100%.

**Accept:** a scan of N prompts across E engines writes exactly N×E engine rows plus extraction
rows · a forced provider failure still writes a row · an org over its ceiling gets refused before
any paid call is made.

---

# Week 2 — the demo surface

### T9 · Extraction v2 · ~6h
Split the flat extraction columns out of the answer row into `extractions` / `mentions` /
`citations` per spec §2.2, versioned by `extractor_version`, with the partial unique index
enforcing one current extraction per answer. Add **`brand_rank`**: record the character offset of
each brand's and competitor's first word-boundary match, sort ascending, take the brand's index.
Keep the existing regex matching — do not replace it.

**Accept:** golden test — a hand-built answer naming three brands produces the exact expected ranks
and citation rows · re-running extraction over stored `raw_response` produces a new
`extractor_version` row and flips `is_current`, with zero provider calls · the old flat columns are
gone.

### T10 · metrics_daily and the Visibility Score · ~5h
Rollup job writing `metrics_daily` per workspace × date × engine, plus a blended row (simple average
across engines, not answer-weighted). Implement PRD §13 exactly. Exclude `runs.type='on_demand'`.
Store `NULL`, not `0`, on an empty denominator. Repoint every dashboard read at the rollup.

**Accept:** the PRD worked example reproduces exactly — MentionRate 0.40, PositionScore 0.717,
CitationRate 0.15, **VS = 44.5** · no read path touches `answers` (assert by grep and by a query
counter in tests) · a period with 0 measured answers yields `NULL`, not `0`.

### T11 · Honest numbers in the UI · ~4h
95% Wilson interval and sample size beside every headline metric, no hover required. Deltas smaller
than the interval render as *"no measurable change"*. Three distinct empty states. Hide the
Visibility Score below 20 answers in the period and show "collecting data".

**Accept:** unit tests for the Wilson bounds against known values · a 3-answer workspace shows
"collecting data", not a score · a +1pt delta on a ±6pt interval renders as no measurable change.

### T12 · Engine adapter interface · ~4h
`app/engines/base.py` with the `EngineAdapter` protocol and `EngineResult` from spec §3. Perplexity
becomes `app/engines/perplexity.py`, the first implementation rather than the shape of the system.
`engines` is a table, seeded by migration. Adapters never touch the DB and never raise.

**Accept:** the Perplexity adapter passes its contract test against a recorded fixture · adding a
row to `engines` plus a module is the entire cost of a new engine — no schema change · a raising
provider yields `status='failed'` and the run still completes as `partial`.

### T13 · Three more engines · ~6h
`openai.py` (Responses API + `web_search`, **forced browsing** or some runs silently measure the
non-browsing model), `gemini.py` (Flash-Lite + Search grounding), `anthropic.py` (web search).
Record one real fixture per provider **before** writing each adapter — do not guess response shapes.
Contract test per adapter, running daily in CI.

**Accept:** one fixture per provider committed, recorded not hand-written · each adapter returns
citations where the provider supplies them and an empty list where it doesn't · a 4-engine scan
completes with per-engine costs in `usage_ledger` · the UI labels each engine's `source_type`, and
Gemini is labelled a proxy for AI Overviews.

### T14 · Onboarding wizard · ~6h
Domain in → fetch the homepage → **one structured LLM call** returning canonical brand name,
aliases, additional domains, ≤10 competitors with their own domains and aliases, and ~25 categorised
prompts (discovery / comparison / purchase / brand). User reviews, edits, approves, and the first
run fires.

Prompt rules that matter: **the majority must be unbranded** · each is a short search-style
fragment under ~12 words, not a sentence · never generate an alias that is a substring of the
canonical name · `branded` is **computed from the prompt text**, never asked of the model.

**Accept:** domain → approved prompt set in under 10 minutes end to end · schema-validated output,
one repair retry then a manual-entry fallback · >50% of generated prompts contain no brand token ·
no generated alias is a substring of the brand name.

### T15 · Citation intelligence · ~5h
Resolve redirects, strip `www.`, lowercase, then classify each citation: own domains → competitor
domains → curated editorial / social / forum / developer lists → `other`. Start the editorial list
at 200–300 domains covering your clients' verticals; it grows weekly and is a real moat. Keep it
server-side. Ship the most-cited-domains view with own vs competitor vs third-party, and competitor
citation gaps.

**Accept:** classification unit tests including redirect chains and subdomains · the view renders
from `citations` with no N+1 · the domain list is server-only and never reaches the browser bundle.

### T16 · White-label report · ~6h
Per-workspace PDF plus a share link. Agency logo and colours, selectable sections (visibility, SOV,
citations, prompts), sample sizes and intervals carried through onto the page. Uses the last
complete run, labelled, if generation happens mid-run.

**Accept:** a PDF generates for a workspace with data and for one without, without erroring · the
share link is unguessable and read-only · branding config changes the output · every number on the
report carries its sample size.

---

## If you fall behind — cut in this order

1. **T16 report** → hand-build one in HTML for the first demo. Painful but survivable.
2. **T15 citation view** → keep the classification, drop the dedicated screen.
3. **T13 down to two engines** — keep Gemini (free tier) and OpenAI (the one prospects ask about).
4. **T11 intervals** → *last resort only.* This is the product's stated differentiator; cutting it
   makes you the same as everyone else with worse data.

**Never cut T5–T8.** Tenancy, worker, ledger and extraction are the things that cost five times as
much to retrofit as to build.

## Week 3+ backlog

Alerts · GA4 (mirror the GSC module exactly) · sentiment as a companion index · recommendations v2
with impact × effort and chart annotations · `llms.txt` and robots AI-bot parsing in the crawler ·
crawler page cap 12 → 500 · Stripe and plan gating from `docs/PRD.md (plan matrix section)` ·
the 50-answer extraction eval set · agent chat over the internal report APIs.
