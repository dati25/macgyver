---
name: solve-the-ticket
description: Close the loop on a Jira-reported customer issue end-to-end — fetch the ticket, download the customer's Rossum org into a ticketsolver branch, understand the problem, design and implement a fix, test it, push to live Rossum, comment on Jira, and archive the change with a README and commit. Use when an SA says "solve this ticket", "fix DC-1234", "close the loop on this Jira bug", or runs `/rossum-sa:solve-the-ticket <TICKET>`.
argument-hint: [TICKET]
---

# Solve the Ticket

Close the loop between a Jira ticket and a validated fix in the customer's Rossum implementation.

> Ticket: $ARGUMENTS

The skill walks through 8 phases. Later phases depend on earlier ones — do not skip. Use tasks to track progress across phases so work can resume if interrupted.

| Phase | Writes? | Gate? |
|---|---|---|
| 1 — Fetch ticket | — | — |
| 2 — Download the org | temp clone + new branch on ticketsolver remote | — |
| 3 — Understand the issue | — | user confirms hypothesis |
| 4 — Design the fix | — | user picks an approach |
| 5 — Implement and test | local edits to cloned repo | — |
| 6 — Push to Rossum | live Rossum (`prd2 push`) | yes |
| 7 — Post Jira comment | Jira comment | yes |
| 8 — Archive | README + commit + push to ticketsolver | per-file approve + final push |

## Phase 1 — Fetch ticket

Fetch the ticket using `mcp__atlassian__getJiraIssue` with `cloudId: rossumai-sandbox.atlassian.net`. Use the minimal field list the `jira` skill documents: `summary`, `description`, `status`, `assignee`, `reporter`, `priority`, `issuetype`, `resolution`, `created`, `updated`, `labels`, `components`, `parent`.

This skill is built for User Support (`US-*`) tickets. If the prefix is anything else, print a one-line warning — "This is a User Support ticket solver; using it on other ticket types isn't recommended, but you can try." — and ask the user to confirm before continuing. On 404, stop with a clear "Ticket `<KEY>` not found" error. On auth error, stop and surface the credential issue so the user can re-authenticate.

Internally capture (do not print to the user): reporter, symptom in one sentence, any queue / hook / field / document IDs mentioned anywhere in the ticket, any explicit reproduction IDs or dates, and any obvious data gaps. These feed Phase 3.

## Phase 2 — Download the org

Resolve the five inputs the `download_org.py` script needs. Gather what's already in context, then ask the user only for what's missing, and confirm before running.

### Input resolution

| Input | Flag | How to resolve |
|-------|------|----------------|
| Organization URL | `--org-url` | Base org URL, e.g. `https://mks.rossum.app`. The script accepts both origin form and `/api/v1` form. |
| Organization ID | `--org-id` | **Do not** parse the `organization` URL from `rossum_whoami` — it points at an internal group object, not the tenant org (fetching it 404s). Instead call `rossum_list_workspaces` and read the `organization` field on any result; that's the real tenant ID. Ask the user as a fallback. |
| Rossum API token | `--token` | See "Token and URL resolution" below. |
| Ticketsolver git URL | `--git-url` | **Default**: `git@gitlab.rossum.cloud:solution-engineering/customers/ticket-solver.git` (SSH). Only override if the user explicitly provides a different URL. If they pass an HTTPS URL, warn them SSH is preferred. |
| Ticket number | `--ticket` | Phase 1 output. |

### Token and URL resolution

1. If `rossum_whoami` already returns a valid identity, reuse the MCP session's cached token and base URL — no further setup needed.
2. Otherwise, if the user pasted a curl command (common), extract the `Bearer <token>` value from the `-H "Authorization: ..."` header and the URL from the curl's URL argument.
3. Otherwise, call `rossum_set_token(token="...", baseUrl="...")` — note `baseUrl` is camelCase, and the URL should be the `/api/v1` form.
4. Call `rossum_list_workspaces` and read the `organization` field on any workspace — that integer is the tenant `org_id`. (`rossum_whoami`'s `organization` URL points to a different, non-tenant object and 404s on fetch.)

### Running the script

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/solve-the-ticket/scripts/download_org.py \
  --org-url <ORG_URL> \
  --org-id <ORG_ID> \
  --token <TOKEN> \
  --ticket <TICKET>
```

`--git-url` defaults to the ticketsolver SSH URL (`git@gitlab.rossum.cloud:solution-engineering/customers/ticket-solver.git`). Pass `--git-url` only if the user explicitly provides a different URL.

Alternatives for the token: set `ROSSUM_TOKEN` in the environment, or pipe the token on stdin. Command-line tokens show up in `ps` output — prefer env var when convenient.

Pass `--force` to wipe an existing `<tmp>/ticketsolver-<TICKET>/` and start fresh. Re-running without `--force` is safe: the script detects an existing clean work dir, reuses it, and re-pulls idempotently (no duplicate commit/push). If the repo is dirty, it aborts with a clear "resume detected, but repo has uncommitted changes" message.

The script creates `<tmp>/ticketsolver-<TICKET>/repo/`, clones the ticketsolver repo, checks out a branch named `<TICKET>`, writes `prd_config.yaml` and `credentials.yaml` under `<TICKET>/organization/`, runs `prd2 pull organization -a` (pulls into `<TICKET>/organization/default/…`), stages `<TICKET>/` only, commits, and pushes the branch. `credentials.yaml` is gitignored — do not add it to any commit.

On success, the script prints the branch name, local repo path, ticket dir, and a one-line summary of what was pulled. After it returns, `cd` into the printed repo path for all subsequent phases so `git status`, `prd2 push`, and edits resolve correctly.

The script raises `RuntimeError` with full command output on failure — surface it and suggest the most likely cause (wrong token, wrong org ID, uncommitted changes from a prior run, no git access, prd2 not installed).

## Phase 3 — Understand the issue

Use `${CLAUDE_PLUGIN_ROOT}/skills/__shared/discovery-checklist.md` as the read plan. Focus on the components the ticket mentions — don't map the whole org unless the ticket's scope actually requires it. If the ticket includes a reproduction annotation or document ID, cross-check via the `rossum-api` MCP (`rossum_get_annotation`, `rossum_get_annotation_content`, `rossum_get_document`, `rossum_list_hook_logs`, etc.) to confirm the symptom against live data.

Then present the hypothesis to the user in this shape:

```
Root cause: <plain-English hypothesis>
Affected: <components>
Confidence: <high|medium|low>

Does this match what you're seeing? Anything to correct or add?
```

**Gate:** do not move to Phase 4 until the user confirms. If they correct or add context, incorporate it and re-present. Loop until they agree.

## Phase 4 — Design the fix

Propose 2–3 approaches. For each, list: what changes, scope (minimal / moderate / broad), and risks / trade-offs. Lead with the recommended option. Default to the most minimal fix that fully resolves the symptom.

Wait for the user to approve one approach or pick an alternative (or ask for a different option). Nothing is written in this phase — the chosen approach lives in the conversation and gets captured in the Phase 8 README.

## Phase 5 — Implement and test

Edit the target file(s) in the cloned repo. Follow the MCP server's editing rules unconditionally:

- **Hook logic** → edit the `.py` source file, never the `code` field in hook JSON. `prd2 push` syncs `.py` back into the JSON.
- **Formula logic** → edit `formulas/*.py`, never the `formula` property in `schema.json`. Same reason.

If a hook `.py` was edited, invoke the `test-hook-locally` skill to run a generated payload against the updated code. Pass an **absolute** path to `--module` — e.g. `/tmp/ticketsolver-<TICKET>/repo/<TICKET>/organization/default/hooks/<HookName>_<ID>.py`. Relative paths resolve against `${CLAUDE_PLUGIN_ROOT}`, not the cloned repo. Iterate edit → test → edit until the hook passes. Show the runner's stdout/stderr after each run. `test-hook-locally` only handles `function`-type hooks — if the edited hook is a webhook, skip local testing and rely on the post-push verification in Phase 6.

If only non-hook files were edited (schema, rule, queue, inbox JSON, or formula `.py`), skip `test-hook-locally` — no equivalent local harness exists. Formula diffs can be eyeballed; schema / rule / queue changes are verified once pushed.

## Phase 6 — Push to Rossum

**Approval gate.** This is the first write to the customer's live Rossum environment. Before running `prd2 push`, print:

```
Ready to push to <ORG_URL>.

Files to sync:
  • <path 1>
  • <path 2>
  …

Proceed? [yes / no]
```

Wait for explicit confirmation.

Before pushing, `git add` each file the user just approved for sync (so `prd2 push -io` has an explicit allow-list). Then run from the ticket directory:

```bash
prd2 push organization/default -io
```

- `organization/default` is the destination — `prd2 push -io` alone fails with "No destinations specified to pull." (verified live).
- `-io` (`--indexed-only`) restricts the push to files in the git index. Combined with explicit `git add`, this produces an `Total objects: 1` push for a one-file fix (verified live on datii).

Report the command output. On failure, do not auto-retry — surface the error and let the user decide whether to re-edit, re-run, or abort.

## Phase 7 — Post Jira comment

Draft an internal SA-team comment on the ticket. Use this shape:

```markdown
**Status:** Fix pushed to <env>

**What was wrong:** <plain-English root cause from Phase 3>

**What changed:** <1–2 sentences + bullet list of files touched>

**Suggested reply to customer:**
> <draft message the SA can copy/adapt and send — include only if appropriate>
```

Show the draft to the user. Options:

- **post** — call `mcp__atlassian__addCommentToJiraIssue` with the same `cloudId` used in Phase 1 (`rossumai-sandbox.atlassian.net`), then confirm `✓ Comment posted to <TICKET>`.
- **edit** — ask what to change, apply the edit, re-show the draft, re-prompt.
- **skip** — move on without commenting.

The "Suggested reply to customer" block is optional — omit it when the change isn't customer-visible (internal config cleanup, observability fixes, etc.).

## Phase 8 — Archive

Three sub-steps. Run them in order.

### 8a — Approve changes

Run `git status --porcelain` in the cloned repo. If nothing is dirty, report "nothing to archive" and exit cleanly — skip 8b and 8c.

Otherwise, for each modified / added / deleted file, in alphabetical path order:

1. Show path, diff (fall back to a structural summary for binary files or diffs over ~500 lines), and a 1–3 sentence rationale (what changed, why, anything noteworthy).
2. Prompt **Approve / Edit / Skip / Stop**:
   - **Approve** → `git add <path>`.
   - **Edit** → ask "what should change?", apply the edit, re-narrate (new diff + new rationale), re-prompt for that file.
   - **Skip** → leave unstaged, move on.
   - **Stop** → abort the rest of Phase 8. Already-staged files stay staged. Print a summary (staged / skipped / unreached) and exit.

**Edge cases:**

- **Deleted files** — show the last-committed content snippet; approving stages the deletion.
- **Renames** — treat as one unit (`R old → new` plus any content diff).
- **Binary or very large files** (> ~500 lines of diff) — fall back to a structural summary ("binary, N bytes" or "N additions, M deletions") and still prompt.

### 8b — Write README

Generate `<TICKET>/README.md` in the cloned repo, drawing prose from Phases 3 and 4:

```markdown
# <TICKET>: <short summary of the fix>

## What was changed

<One or two paragraphs: root cause from Phase 3, chosen approach from Phase 4, and why it fixes the symptom.>

## Changed files

### Modified
- `path/to/file.py` — <brief explanation>

### Added
- `path/to/new_file.json` — <brief explanation>

### Deleted
- `path/to/removed_file.json` — <brief explanation>
```

Omit any section that has no entries (e.g., drop "Deleted" if nothing was deleted). Reference specific object names (hook names, queue names, field IDs) rather than just file paths where possible.

Stage the README with `git add <TICKET>/README.md`.

### 8c — Commit and push

```bash
git commit -m "Fixing <TICKET>"
git push
```

Commit message is exactly `Fixing <TICKET>` (e.g. `Fixing DC-1234`). Upstream tracking was set in Phase 2 (`git push -u origin <TICKET>`), so a plain `git push` is enough here. This push is a gate — confirm with the user before running, then report success or the full error output.

No further Jira writes in Phase 8.

## Safety gates summary

Explicit user approval required at each of these points; all other operations proceed without prompting:

| Gate | Phase | Blocks until approved |
|---|---|---|
| `prd2 push` | 6 | Push to live Rossum |
| Jira comment | 7 | `mcp__atlassian__addCommentToJiraIssue` |
| Per-file approve | 8a | Each `git add <path>` |
| Git push | 8c | `git push` to the ticketsolver remote |

Local reads, local file edits, `test-hook-locally` runs, fetching the ticket, and the Phase 2 branch push (side effect of the download script) do not prompt.
