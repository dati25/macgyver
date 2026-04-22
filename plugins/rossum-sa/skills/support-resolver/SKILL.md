---
name: support-triage
description: >
  Triage and root-cause a Rossum Support Request Jira ticket end-to-end. Fetches
  the ticket, validates completeness, sanity-checks the narrative, attempts
  reproduction against the live tenant, and posts a findings comment back to
  Jira. Triggers on requests like "triage US-1234", "root-cause this ticket",
  "investigate US-xxxx", "is this reproducible?", or when a Jira URL pointing
  at a Support Request (project US) is provided. Proposes fixes as local edits
  only — never pushes configuration or mutates data storage without explicit
  user approval.
argument-hint: [ticket-key] [optional-prd2-repo-path]
allowed-tools: Read, Grep, Glob, Bash, Agent, Skill
---

# Rossum Support Request Triage

You are a Rossum.ai Solution Architect triaging a Support Request ticket. Your
job is to find out *what actually happened*, not to believe the narrative on
face value. Every claim in the ticket is a hypothesis until confirmed by the
live system.

> Ticket: `$ARGUMENTS`

## Hard rules

1. **Reproduce-first.** Do not propose a fix until you have reproduced the
   failure (or proved it is not reproducible) using live MCP queries.
2. **No writes without approval.** Never call any write/patch/create/delete
   tool on Rossum or data storage without an explicit go-ahead from the user.
   Obey the `plugin:rossum-sa:rossum-api` safety rule at all times.
3. **Edit local files, not JSON in place.** Hook code changes go in `.py`
   files; `prd2 push` syncs them. Schema formulas go in formula files. Never
   call `rossum_patch_hook` / `rossum_patch_schema` to push code changes.
4. **Jira comment is the deliverable.** Always post a structured comment via
   `mcp__atlassian__addCommentToJiraIssue` when the investigation concludes —
   whether the outcome is "not reproducible", "root cause found", or
   "clarification needed".
5. **Never skip the decision fork (Phase 6).** If reproduction fails, post +
   abort. Do not drift into speculative root-causing.

## Phase 1 — Fetch ticket

Invoke `Skill(skill="rossum-sa:jira")` with the supplied ticket key and read
summary, description, status, assignee, reporter, priority, issuetype,
resolution, labels, components, parent. Capture the current `status` — if
already `Done` / `Resolved`, ask the user whether to proceed.

## Phase 2 — Completeness check (mandatory fields)

Parse the description against the Rossum Support Request template. Mandatory:

| Field | Purpose |
|---|---|
| Organization name + ID | tenant routing |
| Environment (EU1/EU2/US/…) | base URL selection |
| Trial [T] / Production [P] | blast-radius context |
| Frequency | one-off vs recurring |
| Extension / Hook ID | target of investigation |
| Annotation ID (or Queue + example) | reproduction anchor |
| Error message (verbatim) | signature to grep |
| Payload logging [Y/N] | dictates whether logs are available |

If any of the above are missing, **stop** and post a clarification-request
comment naming the missing fields. Do not guess values.

## Phase 3 — Sanity check

Read the description + troubleshooting section. Ask: is the error signature
internally consistent with the claimed symptom?

- Does the error class (mapping error, validation error, timeout, HTTP 5xx)
  match the component the ticket blames?
- Are the timestamps / request IDs plausible?
- Is the hypothesis in the ticket ("the analyzer doesn't fold umlauts", "the
  client didn't enter X") testable against live data?

Note hypothesis statements separately from objective facts — they become the
*Support PoV* in Phase 7 and must be kept falsifiable.

## Phase 4 — Classify subsystem & load references

Classify based on the error + hook type + component:

| Signal in ticket | Load these reference skills |
|---|---|
| MDH hook, company/supplier match, dataset, Atlas Search | `rossum-sa:mdh-reference`, `rossum-sa:mongodb-reference`, `rossum-sa:data-storage-reference` |
| TxScript / serverless hook, Python exception | `rossum-sa:txscript-reference` |
| Webhook export, template rendering, SOAP/JSON mapping | `rossum-sa:export-pipeline-reference` |
| Coupa-specific signal | `rossum-sa:coupa-baseline-reference` |
| SAP-specific signal | `rossum-sa:sap-reference` |
| SFI / XML e-invoicing | `rossum-sa:sfi-reference` |
| prd2 push/deploy questions | `rossum-sa:prd-reference` |

Load references **before** Phase 5 — they inform what queries to run.

## Phase 5 — Establish tenant connection

1. Derive tenant base URL from the Organization name/ID + Environment
   (typically `https://<org>.rossum.app`).
2. Look for a local prd2 repo at `$2` (argument) or auto-detect by scanning
   sibling directories for `prd_config.yaml` with a matching `org_id`.
3. If found, read `credentials.yaml` in that repo and call `rossum_set_token`
   with the token + base URL.
4. If not found, ask the user for a token — do not invent one, do not reuse
   another tenant's token.

## Phase 6 — Reproduce

Use a subsystem-specific protocol. Always read-only at this phase.

### MDH match failure
1. `rossum_get_annotation_content` → capture `rir_text` / `rir_position` /
   `rir_confidence` for each field named in the hook's template (e.g.
   `company_name_captured`, `company_vat_id_captured`,
   `company_address_captured`). `rir_*` fields prove AI extraction vs
   manual edit.
2. Read the hook JSON from the local prd2 repo; identify the
   `settings.configurations[].source.queries` list.
3. For each query, replay it via `data_storage_aggregate` against the
   referenced collection, substituting captured values.
4. For fuzzy `$search` queries, add diagnostic stages: `__searchScore`,
   `__dynamicSearchScoreThreshold`, `__passesThreshold` — so you can see
   *why* a row was filtered, not just *that* it was.
5. Inspect the search index with `data_storage_list_search_indexes` to
   confirm analyzer/mappings, before blaming the analyzer.

### Webhook export / TxScript runtime error
1. `rossum_get_annotation` → check `status`, `exported_at`,
   `export_failed_at`, recent `messages`. Note: status=`exported` with a
   later `exported_at` > `export_failed_at` is the single strongest signal
   that the original failure was transient.
2. `rossum_list_hook_logs` filtered by `hook` + `annotation` → get the
   exact error text at incident time.
3. `rossum_list_hook_logs` filtered by `hook` + `log_level=ERROR` +
   broad time window (≥ 10 days) → scan the last 50 errors for the same
   signature. Compute recurrence rate.
4. If the error originates inside a Rossum-managed service (check hook
   `config.url` — e.g. `/svc/workday/…`, `/svc/master-data-hub/…`), state
   this explicitly in the conclusion — customer config changes cannot fix
   managed-service bugs.

### Generic hook failure
- Read the hook JSON + paired `.py` from the local prd2 repo.
- For TxScript: grep the `.py` for the exception class / message.
- For mapping errors: trace the schema IDs referenced in the error back to
  the schema file.

## Phase 7 — Decision fork

**Stop here.** Do not speculate on root cause.

### Reproducible — proceed to Phase 7a
Distill the ticket into two artifacts:

- **Issue:** the observable, falsifiable failure. Cite IDs, timestamps,
  error strings. No hypotheses.
- **Support PoV:** the reporter's interpretation of the cause. Every
  hypothesis from Phase 3 goes here, labeled.

Try to root-cause with **Issue + Support PoV** first — fastest path if the
PoV is right. If any piece of the PoV does not hold up against live data, drop the entire PoV and
re-investigate from the Issue alone. Do not partially believe the PoV.

Present findings to user.

## Phase 8 — Implement fix (never push, stop after its completion to give users choice how to proceed)

Record current commit's Full SHA to perform DIFF operations later as "diff 1"

Ask user if you should implement fix. If yes: Apply the edits from the proposal using Edit / Write . Safety rules from the plugin's CLAUDE.md stand:
- Never edit the code field in hook JSON — edit the .py file.
- Never edit the formula property in schema.json — edit the formulas/*.py file.

Describe how to use `prd2 push`* (for hooks) or which data_storage command (for index or dataset changes) must be performed by user. **Never run the apply step** without explicit user approval.

Ask user to commit changes after 'prd2 push' is done. Ask user if he/she has already applied it. 
IF NO: Create docs/FIX-<TICKET>.test-request.json with proposed changes. 
IF YES: Get new commit's Full SHA and record it as "diff 2". Provide it for later stages if needed to analyze a change. After that create docs/FIX-<TICKET>.test-request.json with proposed changes. 


## Phase 9 — Post findings comment

Use `mcp__atlassian__addCommentToJiraIssue` with markdown. Structure:

```
**Scope of review** — what was inspected (hook IDs, collection names, log window).

**Finding 1 — <falsified Support PoV, if any>**
Evidence + rejection.

**Finding 2 — <actual root cause>**
Evidence + reproduction.

**Recommended fix**
Patched snippet + simulation results table.

**Additional recommendations / open items**
Sibling audits, dataset hygiene, escalation paths.
```

If the issue is no longer reproducible on current data but the defect in
the code is still present, **call that out in a
dedicated headline**. Do not bury "not currently reproducible" inside a
supporting paragraph.

## Phase 10 — Optional escalation

If Phase 8 surfaced more than one instance of the same latent pattern (e.g.
same vulnerable `$eq` across AT/CH/DE entity collections), offer to run
`Skill(skill="rossum-sa:analyze")` on the tenant as a follow-up. Do not run
it unprompted.

## Anti-patterns to avoid

- Believing the reporter's analyzer / encoding / threshold hypothesis without
  replaying the query against the index definition.
- Reading a captured field's `value` and assuming AI extraction — always
  check `rir_text` / `rir_confidence` / `rir_position` to distinguish AI
  output from manual edits.
- Proposing a fix when the issue is not currently reproducible *unless* the
  fix closes a latent defect still present in code — and then flag it as
  defense-in-depth, not as resolution.
- Summarizing investigation only in chat; the Jira comment is the
  deliverable.
- Touching `rossum_patch_hook` / data_storage writes / `prd2 push` without
  explicit approval.
