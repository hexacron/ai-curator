# osint_tracker

Tracks **emerging** OSINT repositories: those created within the last `window_days` (default 90), whose
first commit is also recent (at most `first_commit_grace_days`, default 14, before creation, so a short stretch
of private work does not disqualify a project; a longer history means a mirror or re-upload). Each daily run records every candidate's star and fork
counts, enriches and classifies the promising ones (rules, then an optional LLM review), scores them, and
writes ranked boards. Star history cannot be back-filled (GitHub's stargazer endpoints are not available),
so the earlier this runs, the better the momentum data.

```
python -m osint_tracker [--config config.json] [--data-dir data/osint] [--dry-run] [--no-llm] [--review-limit N]
python -m osint_tracker.site [--data-dir data/osint] [--out docs/osint]
```

Needs `GITHUB_TOKEN` (environment or `.env`); `ANTHROPIC_API_KEY` enables LLM review. Tests:
`pip install -r requirements-dev.txt && pytest`.

## What a run does

1. **Discover** – for each topic in `topics`, search repositories with `created:` inside the window
   (`fork:false archived:false`). A window with more than 1000 results is bisected by creation time,
   because the search API never returns more than 1000 per query.
2. **Triage** – from fields the search response already carries:
   - `tier1`: stars >= `max(1, ceil(age_days / floor_days_per_star))` (1 star for the first 15 days,
     6 by day 90). Recomputed every run while the repository is inside the window.
   - `batch_publisher`: its owner has >= `batch_publisher_threshold` candidates in the window.
3. **Snapshot** – today's `stars`/`forks` for **every** discovered repository, tier-1 or not, so a
   repository that jumps from 3 to 300 stars in a week already has history when it crosses the floor.
4. **Enrich** (tier-1 only) – one batched GraphQL lookup by node id (10 repositories per point):
   commits and authors over 30 days, issues, PRs, releases, owner profile, README, root files, CI.
   Tier-1 repositories that left the window are refreshed by the same lookup, so their history keeps
   growing; deleted or private ones are marked `gone` and kept. The first commit is fetched once per
   repository (one REST call) and cached.
5. **Classify** – rules over name, description, topics and the README head (`classify.py`), then the LLM
   review (below), then human curation (`curation.json`).
6. **Identify** – copies of an earlier repository (identical README, or same name and description).
7. **Score and rank** – `score.py`, `rank.py`; see below.
8. **Prune** – repositories that never passed triage are dropped `prune_grace_days` after leaving the window.

Nothing is written if any GitHub call fails or discovery returns nothing, so a bad run cannot punch a
hole in the history. README text is analysed in memory and never stored.

## Classification

| Field | Values |
|---|---|
| `scope` | `core` (collects/analyzes public information), `adjacent` (infrastructure/ASM, threat intel, or unclassifiable), `out` (offensive kits, training, unrelated scrapers) |
| `domain` | identity-lookup, breach-exposure, crypto-financial, media-forensics, disinformation, dark-web, knowledge, workspace-analysis, web-capture, transport-sensor, geospatial-imagery, situational-awareness, social-media, infrastructure-asm, threat-intel, `general` (suite spanning several domains or generic OSINT) |
| `form`, `ai_role` | cli, web-app, bot, mcp-server, agent-skill, ... / none, llm-assisted, agentic, skill-mcp-native |
| `sensitivity` | `none`, `caution` (listed everywhere with a visible label), `high` (never published on any board) |

**Rules** count evidence per pattern as distinct matches (name/description double, README head once,
author-chosen topics at most one point per domain, so topics can confirm but never decide). README text can
only reinforce an out-of-scope signal found in the description, never create one. Text in other languages
is left `adjacent` with no domain, which is exactly where the LLM review earns its keep.
`tests/gold_repos.json` regression-tests the rules; they were tuned on it, so it guards against drift and is
not an accuracy figure.

## LLM review (optional)

One call per new or changed repository, to the model in `llm.model` (a stronger `llm.escalation_model` is
asked once more when the first answer's confidence is low). The reply is JSON-schema constrained
(`output_config.format`) and validated again here. It supplies scope, domain, form, AI role, posture,
sensitivity and its tags, a 0-5 utility rating, a summary, strengths and risks.

**Trust model.** READMEs are attacker-controlled. The model gets no tools and no secrets, only a sanitised
digest (HTML comments, zero-width/bidi/Unicode-tag characters and encoded blobs removed; the data delimiter
neutralised). It can only *reduce* exposure:

- sensitivity is the maximum of the rules and the model; the model never lowers it
- the model can narrow scope (core -> adjacent -> out); it can widen it only where the rules abstained
  (no evidence, or a weak out-of-scope call)
- utility counts only if the model quotes the README verbatim (quotes are checked against the digest)
- below `min_confidence_to_override` it changes nothing except raising sensitivity
- stars, license and activity never come from the model

**Budget and failure.** At most `max_calls_per_run` API requests per run (escalations count), most-starred
repositories first; the rest wait for the next run. Reviews are cached by a key over the prompt version
(`PROMPT_VERSION` in `review.py`; raising it re-reviews everything, which costs cents), model, repository id and everything the model saw, so unchanged repositories cost nothing. A stored review
keeps applying until a new one replaces it. A rejected key stops LLM use for the run; three failures in a
row do the same; neither fails the run. Repositories the rules reject with confidence are never sent.

**Key.** `ANTHROPIC_API_KEY` as a GitHub Actions secret (passed only to the tracker step) or in `.env`.
Without it the tracker runs rules-only and still applies any stored reviews. `--no-llm` ignores the API and
stored reviews alike; `--review-limit N` overrides the budget for a run (`0` reviews nothing).

## Scoring

```
total    = 100 * relevance * integrity * weighted mean of available components
rank_key = total - confidence_penalty * (1 - confidence)
```

| Component | From |
|---|---|
| momentum | star/fork growth per day (lifetime average blended into the last 7 days as snapshots accrue), as a percentile within its cohort (emerging vs established) |
| activity | commits in 30 days, recency of last commit, release recency |
| maturity | license, README size and sections, tests, CI, packaging |
| community | issue closure, PRs, distinct authors, fork-to-star ratio |
| utility | the model's 0-5 rating, only if backed by a verified quote; otherwise left out and the weights renormalise |

Two weight profiles: `rising` (momentum-heavy) and `overall`. `integrity` multiplies by 0.85 (batch
publisher), 0.9 (owner account created around the repository), 0.8 (100+ stars, almost no forks), 0.85
(watchers out of proportion), 0.8 (unclosed issue pile), 0.5 (clone); below `unverified_below` a
repository is left off every board. These are signals to review, not accusations.

## Outputs

**`data/osint/state.jsonl`** – line 1 is a `_meta` header; every other line is one repository keyed by its
numeric `id` (stable across renames; earlier names in `names`). Counters are day-indexed from `snap_start`
with `null` for days not observed. Besides the discovery fields it holds `enrich`, `earliest_commit`,
`review` (the validated model answer and its cache key), `class`, `clone_of` and `score`. **This file is
public in the repository.** With `privacy.redact_high_sensitivity` (default on), repositories of `high`
sensitivity keep only identity, labels and counters: description, topics, homepage and model-written text
are dropped before saving. That covers repositories that never passed triage (the rules over their
description decide) and every candidate of an owner who has a `high` repository, because lookup-bot
accounts publish many near-identical repositories. What remains public is each repository's `owner/name`
and URL, since the history is keyed to it.

**`data/osint/boards.json`** – derived each run: `rising`, `new`, `established`, `adjacent`, and a board per
domain, each entry with rank, rank change since the previous run, score, components, flags, and the model's
summary/strengths/risks; plus `excluded` counts and a `rejected` list (out-of-scope and clone repositories,
with the reason) for tuning. Descriptions and model text derive from untrusted input: escape them wherever
they are rendered.

**`data/osint/curation.json`** (optional, hand-written) – overrides applied last:

```json
{"pin": {"owner/repo": {"scope": "core", "domain": "social-media", "sensitivity": "none"}},
 "block": ["owner/other-repo"]}
```

A pin may set `scope`, `domain`, `form`, `ai_role`, `sensitivity` (including lowering it: a person decided);
a blocked repository is out of scope. Repositories match by current or former name, or id. A mistake in the
file raises instead of being ignored.

Board gates: tracked, not gone/archived, scored, not a clone, not `high` sensitivity, integrity at or
above `unverified_below`. `rising` also needs emerging status and confidence >= `min_confidence_rising`
(`caution`-labelled repositories are included); every board allows at most `owner_cap` repositories per owner.

## Site

```
python -m osint_tracker.site [--data-dir data/osint] [--out docs/osint]
```

Turns `boards.json` into `docs/osint/index.html` (one self-contained file) and `docs/osint/data.json`. The
page has a tab per board (Rising, New this week, Established, Adjacent, By domain), search, sort (board
rank, score, stars, newest, fastest growing), domain chips, form and AI-role filters, a toggle for
caution-labelled repositories, rank-change arrows, a NEW badge for repositories under a week old, star
sparklines, and a per-repository score breakdown with integrity flags, confidence, and the model's
strengths and risks.

Safety. Repository text is untrusted. It is embedded as JSON escaped so it cannot close the script
element, the page only sets it through `textContent`, links must be absolute http(s), and a
Content-Security-Policy (`default-src 'none'`) makes the browser itself refuse any request to another
origin. The generator drops the `rejected` list and any `high`-sensitivity entry even if one reached the
boards, and refuses to build (instead of publishing an empty page) if `boards.json` is missing or corrupt.

The page's pure logic (`site_template/logic.js`) is checked by `tests/js/logic_check.js`, which pytest runs
when `node` is installed (it is on GitHub's runners).

**Workflow order** (`.github/workflows/curator.yml`): `curate` (AI collection, commits README/docs) ->
`track-osint` (tracker, builds the page, commits `data/` and `docs/osint/`) -> `deploy` (publishes `docs/`
from the branch head). Deploy is last so one run updates both pages, and it runs even if an earlier job
failed, so the pages stay up with whatever did succeed.

## Configuration

Defaults live in `config.py`; override any of them in the `osint` section of `config.json`
(see `config_template.json`). Unknown keys are rejected, so a typo cannot silently change what is tracked.
