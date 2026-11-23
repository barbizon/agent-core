---
name: code-check
description: Code review checklist for finished code — Review code (a review checklist, findings by category) and Code Scoring (a score: 100 minus deductions, caps, grade A–D). Writes the review-findings and grade docs.
---
# Code check

Two checks of a **finished** change. Report only; never edit code. Your team's
own rules come as **additions** (below the instructions, when there are any):
check those items too, and where one differs from an item here, it wins. The request says which code: **this session's pull
request**, or a **pull request link**. Neither: ask which.

## 1. Get the change set (both checks)

**A pull request link:** `read_pull_request(pr=link)` gives its branches,
commit and changed files; then each file's diff with `read_pull_request(pr=link,
file=path)`, several calls in one reply. That diff is the change. For callers
and tests, read the repo under the code root (`../<repo>`); it may be on another
branch, so trust the diff over it. No local git needed.

**This session's pull request:** the repo is the workspace; when the workspace
holds many repos, the one whose current branch carries the session's ticket key
(none or several: ask). `run_command` (cwd = repo): `git remote get-url origin;
git branch --show-current`, then `read_pull_request(repo="<workspace>/<repo>",
branch=…)`. Found: the base is its destination branch; name the PR in the doc.
`found: false`: say there is no open pull request yet and check the branch
against the repo's main branch (develop, else main or master; say which). Then the local change, which also has uncommitted work:
`B=$(git merge-base <base> HEAD 2>/dev/null || git merge-base origin/<base> HEAD); echo $B; git rev-parse --short HEAD; git status --short; git diff --stat $B`
No such base? Try main/master, say which. Then the diff **file by file**, several
`run_command` calls in one reply: `git diff -U5 <base> -- <file>` (command
output keeps only ~6,500 characters, so one file per call; a huge file: diff
it by hunks or `read_file` around them). Skip generated files: mocks,
generated code (`*.pb.go`), lock files (`go.sum`, `package-lock.json`), swagger. Untracked files (`??`) are part of the change:
`read_file` them. Read callers and tests only as needed.
**Score only lines this change touched**; old problems nearby don't count.

## 2. Review code → doc `review-findings`

Go through every item for the changed code. A finding needs `path:line`,
the evidence (the code), and the fix. One root cause = one finding. Examples
are in Go; apply the same idea in the change's language.

**Security** — S1 Access control: who the caller is comes from the auth token,
never from a caller parameter; ownership is part of the query (`WHERE owner_id
= ?`), not a filter after; one tenant never sees another's data. S2 SQL is built
with a query builder or bound parameters, never `fmt.Sprintf` / `+` /
interpolation; dynamic WHERE, IN lists and paging too.
**Testing** — T1 Unit tests cover every new branch, error return, boundary
(empty, nil, zero/negative, expired). T2 A new or changed endpoint or consumer
has a test through its real entry point (router, handler). T3 Existing mocks
and test helpers are reused, not hand-rolled.
**Error handling** — E1 Errors are wrapped with context inside
(`fmt.Errorf("…: %w", err)`) and logged once, at the top. E2 Logs where needed:
a meaningful operation, every error path, each external call (DB, HTTP, cache).
**Clean code** — C1 Comments say why, not what; no history, no restated
signature. C2 A doc comment sits on what it documents. C3 Tests exercise
business logic, not setters, logging, config, constants. C4 A test can fail (no
`len(x) == len(x)`, no call-happened-only asserts).
**Correctness** — R1 Idempotency where retries/duplicate events happen (key,
dedup, upsert, unique constraint). R2 No swallowed errors (`_ =` on a failing
call, log-and-continue). R3 Concurrency: shared state guarded, no loop var
captured in a goroutine, the context passed to every DB/HTTP/cache call,
goroutines end.
**Performance** — P1 No needless copies of large values in loops and returns;
no query or remote call inside a loop that one batch call could replace.
**Design** — D1 No re-written standard library or shared helper. D2 Function
≤ 100 lines. D3 File ≤ 700 lines. D4 No god type with a long method list. D5 No
duplicated logic. D6 Dependencies are interfaces, injected through the
constructor. D7 Layers handler → service → storage only: no SQL or transactions
in a handler, no HTTP types below it, no business rules in storage; services
keep no state.
**Style** — Y1 Standard library constants over literals (`time.RFC3339`,
`http.MethodGet`). Y2 Constants for repeated strings/numbers (statuses, types,
keys).
**API** — A1 Responses and errors follow the service's existing shape, no
ad-hoc ones. A2 Dates and numbers in the format the API already uses.

**Doc `review-findings`:**
1. Header line: `**Repo:** … · **PR:** #N (its link) or none · **Commit:** short hash (+ uncommitted) · **Base:** branch · **Checked:** date`.
2. `**Result:** N findings (Bug n · Warn n · Info n)`; worst first in one sentence.
3. Table `# | Item | Sev | Where | What's wrong | How it should be` — Sev is
   Bug (wrong result, security, missing test), Warn (a rule broken), Info.
   Ranked: correctness, security, missing tests first; no padding.
4. `**Passed:**` the item ids with nothing found, one line; `**Not checked:**` any
   item you couldn't check, and why.

## 3. Code Scoring → doc `grade`

Start at 100. Use the `review-findings` doc when it is for the same commit; else
do step 1 and the checklist first. Also check **ticket fit**: read the session's
ticket (the ticket tools that are on; the key in your instructions, else ask;
ask only for the fields you need, e.g. summary, description, status) and compare
each acceptance case to the code. The `product-requirement` doc, if there, may
already have it.

| # | Finding | Counts when | Each |
|---|---|---|---|
| 1 | Missing | The ticket asks for it; the code doesn't do it | −20 |
| 2 | Partial | One acceptance case missing or different | −10 |
| 3 | Extra | Code does what the ticket didn't ask (changes existing callers → Bug) | −5 |
| 4 | Critical | Wrong money or balance, lost data, security hole (another user's data, string SQL), crash on a real path, broken public or shared-library contract | −30 |
| 5 | Bug | A real flow gives a wrong result (status, event, filter, count) | −20 |
| 6 | Risk | Fails in a narrower real case: swallowed error, no rollback, race, invisible failure | −8 |
| 7 | Warning | A rule broken, result right: naming, a needless copy, recover/panic, re-written helper, dead code | −3 |
| 8 | Nit | Wording, comments, formatting | −1 (max −5) |
| 9 | No unit test | New rule/branch with no test that fails if it broke | −5 |
| 10 | No entry-point test | New/changed endpoint or consumer without one | −8 |

**Caps (after deductions):** build or a test fails → max 40 · any Critical →
max 49 · any Missing → max 69.
**Build/tests:** run the project's build and tests once (Go: `go build ./... &&
go test ./...`; a linked pull request that isn't the checked-out code: not run).
Failing because of the sandbox (no module cache, no network) is **not run**, not
failed: no cap, say so.
**Grades:** 90–100 A merge (fix warnings/nits if cheap) · 75–89 B fix the
findings, no second review · 60–74 C fix, then review again · 0–59 D send back.
**Fairness:** one root cause = one finding, one severity (the best fit, never
twice); only lines this change touched; don't count near-impossible cases,
rejected alternatives, stale PR title/description/commit message, known false
positives. Something the ticket never asked for is not Missing: list it for the
PM, no deduction.

**Doc `grade`:**
1. `**Score: N / 100 · Grade X · <what to do>**`, then the header line as in review-findings plus `**Ticket:** KEY`.
2. Table `# | Finding | Count | Each | Total`, rows with count 0 left out, then
   the caps applied and why, and the build/test line (passed / failed / not run).
3. Table `# | Type | Where | What | Fix` per finding (ids B1, W1… as in review-findings).
4. **Must fix before the PR:** every Critical, Bug, Missing, Partial, missing
   test. **Fix or explain in the PR:** Risk, Extra. **Only if grade B or worse,
   or a one-line fix:** Warning, Nit.
5. **For the PM:** items the ticket never asked for (if any).

## Finish

Write the doc(s) in one `write_session_docs` call. Reply in 2–3 lines: the
count of findings (or score and grade) and the worst one.
