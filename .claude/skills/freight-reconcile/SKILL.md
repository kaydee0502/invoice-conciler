---
name: freight-reconcile
description: Reconcile carrier freight invoices and credit notes against BlueFin shipment records and rate contracts, producing reconciliation-report.json and memos/. Drives the freight-recon flowstate graph end to end. Use when asked to run, re-run, or resume a freight billing reconciliation.
---

# Freight billing reconciliation

You are the **orchestrator** of a reconciliation run. You do not reconcile
anything yourself. Every number in the report is produced by a script or a
worker inside the `freight-recon` flow, and every output passes a validator
before the run advances. Your job is to drive the graph, decide what to do when
a step fails, and report what happened.

> Money moves on this output. If you notice yourself computing an expected
> amount, choosing a disposition, or editing a report or memo by hand, stop:
> that is the system's job, and a hand fix makes the run invalid as evidence.

## Inputs and outputs

| | Path | Notes |
|---|---|---|
| Ground truth | `data/shipments.json` | one record per shipment |
| Authority on price | `data/contracts/*.md` | prose; one per carrier |
| Documents to reconcile | `data/invoices/*` | invoices + credit notes; format varies by carrier (txt / csv with CRLF / json) |
| Report | `<run_dir>/reconciliation-report.json` | must conform to `report.schema.json` |
| Memos | `<run_dir>/memos/*.md` | one per non-`accept` line or finding |

At the end of a successful run, copy the report and memos to the repo root
(`reconciliation-report.json`, `memos/`). That is the only file-writing you
do yourself, and the files must be byte-identical copies.

## Design in one paragraph

Deterministic work runs as scripts: parsing, matching, arithmetic, totals and
invariants. LLM workers run only where judgement is the value: turning
contract prose into a structured rate spec, deciding the exceptions the rate
engine cannot settle, and writing memos. Each worker gets a fresh, small
context: one contract, or one exception bundle, or one memo. Nothing a worker
outputs is trusted until a script has checked it, and at scale the number of
workers grows with the number of exceptions, not with the number of lines.

## Pipeline (flow: `factory/flows/freight-recon/`)

Status: `[ ]` not built · `[~]` drafted · `[x]` built and tested

| # | Node | Runner | What it does | Validation after it |
|---|---|---|---|---|
| 1 `[x]` | `ingest` | script | Content-sniffed format adapters (`reconlib/adapters/`) → `ingest.json`: normalized lines keyed `DOC#n`, document headers, and **anomalies** | **Hard failure** (exit 1, no output, full list in `ingest-errors.txt`): unclaimed file, unparseable money, unknown header/key/column, duplicate doc id. **Anomaly** (recorded and passed downstream): lines ≠ printed total, components ≠ line total, duplicate consignment, credit note pointing at nothing. Tests: `tests/test_ingest.py` |
| 2 `[x]` | `plan_contracts` → `specs_fanout` → `specs_join` → `check_specs` | script → dynamic_fanout of subflow `contract-spec` (one per contract) → join (reducer `collect_specs.py`) → script | Per contract: extraction A (opus) and B (sonnet) write `rate-spec.json` candidates (declarative; `reconlib/ratespec.py` is the only evaluator); `compare` prices a probe grid (real shipments × every service/flag × every tier boundary ±0.5/1) with both. Agree → done. Disagree → extraction C (opus) → `vote` (majority of 3, else exit 1). | Worker self-checks with `reconlib.speccheck`; flowstate validates schema at `finish`; `check_specs` re-verifies every planned carrier has a spec for the right carrier that passes speccheck. Tests: `tests/test_ratespec.py`, `tests/test_flows.py` |
| 3 `[x]` | `match` | script | Exact match on (carrier, normalized ref) → `match.json`: `shipment_id` or null, credit-note lines linked to the invoice line(s) they correct, fact conflicts (invoice vs shipment) tagged `pricing`/`informational`, near-miss `hints` (one edit away, other carrier, not delivered). Hints never match. | Every ingest line appears once; a printed fact with no comparison rule is a hard failure (system gap, not data). Runs before `plan_contracts`. Tests: `tests/test_match.py` |
| 4 `[x]` | `price` | script | `reconlib/price.py`: agreed spec × **shipment record** → expected amount, delta, and a provisional disposition from an explicit rule id (`R_MATCHES`, `R_OVERBILLED`, `R_DUPLICATE`, `R_UNDETERMINED`, `R_UNMATCHED`, `R_CREDIT_*`, `R_ADJ_*`, …). The delta is explained by matching billed components to contract charges (`AMOUNT_DIFFERS`, `CONDITION_NOT_MET`, `UNLISTED_NOT_PAYABLE`, `UNLISTED_CONTRACT_SILENT`, `PRINTED_ARITHMETIC`). Invoice findings: volume adjustment (vs consignments tendered that month), total/count anomalies. `review: true` only where a rule leaves a real choice | every line gets a disposition or the script exits non-zero; schema `definitions/price.json`. Tests: `tests/test_price.py` (fictional carrier) |
| 5 `[x]` | `adjudicate` | agent (conditional) | Runs only when `price` emits `review_count > 0` (edge conditions); otherwise `price → assemble`. Decides disposition + justification for review lines only, within each line's `allowed_dispositions` | schema `definitions/adjudication.json`; `assemble` I9 rejects decisions on non-review lines, outside allowed dispositions, or missing lines. Never touches amounts |
| 6 `[x]` | `assemble` | script | `reconlib/assemble.py` → `reconciliation-report.json` + `report-index.json` (memo items) | Refuses unless I1–I9 hold: report.schema.json; every line once; delta arithmetic; null⇒escalate; totals vs printed; summary re-adds; no rupee disputed twice; every anomaly/fact conflict recorded; adjudication bounds. Tests: `tests/test_assemble.py` |
| 7 `[x]` | `plan_memos` → `memos_fanout` → `memos_join` → `publish` | script → dynamic_fanout of subflow `memo-batch` (per-carrier batches ≤ 8) → join → script | Worker writes one memo per non-accept item from a brief; `check_memos` loops back once with feedback, then fails the run. `publish` copies report + memos to run dir and repo root with a sha256 manifest | Checker: every item has a memo naming invoice/ref/disposition, quoting the key amount and **no rupee figure absent from its brief**. Publish: memo set == non-accept items exactly; stale memos removed. Tests: `tests/test_memos.py` |

## Running it

1. **Preflight**, in this order. Stop and tell the user if any step fails:
   - `orchestrator/.venv` exists (else run `orchestrator/setup.sh`)
   - `jq`, `tmux` and `git` are on PATH, and `git config user.email` is set
   - the scripts under `orchestrator/bin/`, `.claude/hooks/` and the flow's `scripts/` and `gates/` are executable (Windows downloads drop the bit)
   - your session id is present (`CLAUDE_CODE_SESSION_ID=` from the SessionStart hook)
2. **Bootstrap**:
   ```bash
   orchestrator/bin/flowstate bootstrap \
     --flow-dot factory/flows/freight-recon/freight-recon.dot \
     --run-descriptor "recon-<yyyymmdd>" \
     --orchestrator-session-id "<your session id>" \
     --supervision afk
   ```
   Inputs are read from fixed repo paths, so there are no seed variables unless the flow declares some.
3. **Drive the parent** with `orchestrator/bin/flowstate advance --run-dir "$run_dir"`.
   Script nodes and conditional edges are chased automatically, so advance returns
   only when the parent lands on something that needs you:
   - **A `dynamic_fanout`** (`specs_fanout`, `memos_fanout`): run
     `factory/flows/freight-recon/bin/run-fanout.sh "$run_dir"` (tool timeout 600000;
     run it in the background for large runs). It starts branches within flowstate's
     concurrency window and drives each child with `bin/drive-branch.sh`, applying the
     failure policy below mechanically and logging each decision with
     `flowstate event`. Per-branch logs go to `$run_dir/branch-logs/`. When it exits 0,
     `advance` the parent again: it passes the template node and fires the join.
   - **`adjudicate`** (only when `price` flagged lines for review): spawn, wait and
     finish it per `.claude/skills/graph-orchestrator/SKILL.md`.
   - **`kind: end`**: go to step 4.
   Don't hand-drive child runs. The scripts keep hundreds of worker lifecycles
   out of your context, which is what makes large runs reliable.
4. **Finish**: `publish` has already copied the report and memos to the repo root
   and written `publish-manifest.json` (sha256 of each file). Tear down the run's
   tmux sessions (`tmux ls`; kill each `B1_*` session by exact name), then report the
   run dir, the summary block, and any `validation_feedback_sent`,
   `worker_respawned` or `run_stopped` events.

Variables you may seed at bootstrap (defaults in the flow):
`invoices_dir`, `shipments_path`, `contracts_dir` (inputs); `publish_dir` (default
`.`); `use_spec_cache` (`yes`/`no`, default yes: agreed rate specs are reused while
the contract, the spec schema and the extraction prompt are all unchanged).

## Failure policy

This is the part `graph-orchestrator` leaves open.

| Situation | Action |
|---|---|
| `run-fanout.sh` exits non-zero | A branch stopped after its one retry. Read `$run_dir/branch-logs/<branch>.log` and the child run's events, then report to the user. Once the cause is fixed, `bin/drive-branch.sh <child_run_dir>` resumes that child from its unfinished node. |
| Script node or gate fails | **Do not retry blindly.** Scripts are deterministic, so a rerun gives the same result. Read the error, surface it to the user, and stop. This is a bug in the data or the system, not noise. |
| Worker output fails schema validation | Respawn once with the validator's feedback appended to the prompt. There is no live worker to message: agentctl kills a worker as soon as it writes `completion.yml`. (`drive-branch.sh` does this for child flows.) |
| Worker dies or stalls | Kill it and respawn once. Log a `flowstate event`. |
| Fails after the respawn | Stop the run and report the node, the feedback and the transcript path. **Never** `advance --force` past a money-bearing node. |
| Contract extractions disagree | Not a retry case. The contract is ambiguous in a way that matters, so escalate the affected rules and tell the user which clause is involved. |
| Worker asks a question (`awaiting_human`) | Relay it to the user verbatim. Don't answer on the user's behalf. |
| Stall with no child processes | `tmux capture-pane` first. A trust dialog or permission prompt looks like a stall. |

## Domain rules every stage must respect

These are fixed decisions. The stage prompts and scripts implement them, and
they are listed here so the orchestrator can recognise a violation.

- **The contract is the authority on price, and shipments.json is the
  authority on facts** (weight, distance, service, handling). The invoice is
  the claim being tested. When the invoice's facts disagree with the shipment
  record, price from the shipment record and flag the conflict.
- **Dispositions:**
  - `accept`: billed amount equals expected, within ±₹0.01.
  - `dispute`: the contract determines a different amount and we can say by how much.
  - `escalate`: the contract does not determine the amount (out of the rate card, an undefined boundary, an unmatched line, conflicting facts that need a human).
- **Credit notes** are documents with their own lines. Each one links to
  the invoice line it corrects, and a line and its credit are never both
  counted in dispute.
- **Invoice-level adjustments** (for example a volume discount on the
  invoice total) go in `invoice_findings`, not on individual lines.
- **No discrepancy is dropped.** Every ingest anomaly and every
  invoice-vs-shipment fact conflict must land in the report, either on its
  line (`justification`/`notes`) or as an `invoice_finding`. `assemble`
  enforces this.
- **Memos are for non-`accept` items only** (PROBLEM.md: "every line or
  finding your system judges as anything other than `accept`"). An
  accepted discrepancy, such as a wrong printed weight that doesn't change
  the price or an overcharge fully corrected by a credit note, is recorded
  in the report but gets no memo.
- **Rounding:** money is computed exactly (Decimal), rounded to 2 dp once at
  line level, and totals are sums of rounded lines.

## Machinery notes (found while building; see DESIGN.md)

- **Fixed in flowstate:** `_fire_join` used to skip gates on a join's
  outgoing edge and left an end node downstream of a join `in_progress`.
  Both are fixed (see DESIGN.md), with regression tests in
  `orchestrator/tests/`. Conditions on a join's outgoing edge are still
  impossible, because the parser rejects conditions on a single outgoing edge.
- **Fixed in flowstate:** a join after a `dynamic_fanout` used to fire only if
  it had a reducer, and an accumulating reducer saw each branch's stale copy of
  its summary variable. Both are fixed (see DESIGN.md, tests in
  `orchestrator/tests/test_fanout_join.py`). If a parent ever looks stuck after
  all children end, `flowstate complete-subflow --branch <id> <parent_run_dir>`
  re-fires that branch's completion hook, and it is safe to repeat.
- Subflow children of a `dynamic_fanout` push their outputs and fire the
  join reducer themselves on completion. After the last child ends, one
  `advance` on the parent passes through the template node and fires the join.
- `script=` takes a path only, not arguments; use thin wrapper scripts.
- Every `{identifier}` in a prompt template is a placeholder, so keep literal
  braces out of prose (JSON examples are fine, since `{"` never matches).

## Open questions (resolve as we build)

- [x] Rate spec format: declarative, evaluated by one engine (decided).
- [ ] Adapters for new carrier formats at scale: have a worker draft an
      adapter under a gate that sums lines to the printed total, or keep
      hand-written adapters only?
- [ ] Exception batching size for `adjudicate` (per invoice? per carrier?).
- [ ] Restrict worker MCP/settings, since workers currently start the user's
      GitHub MCP container.
