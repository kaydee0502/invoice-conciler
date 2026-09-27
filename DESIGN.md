# Freight billing reconciliation: design notes

## Architecture

**A hybrid: a flowstate graph for control, driven by an orchestrator skill.**
Money moves on the output, so the sequence, the gating and the audit trail
belong to software that cannot get distracted (brief §6). The skill
(`.claude/skills/freight-reconcile/SKILL.md`) gives the orchestrating agent
the domain rules and a failure policy. The graph
(`factory/flows/freight-recon/`) owns everything else.

```
ingest ─ match ─ plan_contracts ─┬─(all cached)─────────────────────────┐
                                 └─ specs_fanout ─ [contract-spec] ─ specs_join ─ check_specs
check_specs ─ price ─┬─(review_count = 0)──────────────┐
                     └─ adjudicate (agent, rare) ─ assemble ─ plan_memos ─ memos_fanout ─ [memo-batch] ─ memos_join ─ publish
contract-spec: load_item ─ extract_a (opus) ─ extract_b (sonnet) ─ compare ─┬─ done
                                                             └─ extract_c (opus) ─ vote ─ done
memo-batch:    load_item ─ write_memos (opus) ─ check_memos ─┬─ done
                                                             └─ write_memos (one retry with feedback)
```

**Scripts vs agents.** The rule: a model runs only where its judgement is the
product, and nothing it produces is trusted until code has checked it.

| Work | Runs as | Why |
|---|---|---|
| Parsing 3+ invoice formats | script (`reconlib/adapters/`) | Deterministic and exhaustively checkable; a model could drop a line silently |
| Matching lines to shipments | script | Exact-ref join; fuzzy "helpfulness" would pay the wrong shipment |
| Contract prose → pricing rules | **agent**, twice, cross-checked | Reading prose is judgement; it is small (per contract, not per line) and is verified by behaviour, not by trust |
| Pricing, deltas, dispositions | script (`ratespec.py`, `price.py`) | Arithmetic on money must be exact and identical on every run; every disposition has a named rule |
| Genuinely open questions (e.g. an unlisted charge under a contract that is silent) | **agent**, conditional | Judgement, but bounded to the rule's allowed dispositions; never amounts |
| Report assembly | script (`assemble.py`) | The last line of defence; refuses to write on any broken invariant |
| Memos | **agent**, batched | Communication is the product; each rupee figure is checked against the item's brief |

**How the design scales.** Per-line work is script-only and linear (measured
at 50× the sample: 19,000 lines, 850 documents, about 9 s from ingest to
assembled report, with results exactly 50× the sample). Model work grows with
**contracts** (extraction, cached by contract hash) and with **exceptions**
(memos, batched per carrier), not with invoice volume. Each worker's context
is one contract or one batch of at most 8 memo items, so no context grows
with the run.

## What is validated, where, and why there

Validation sits at every point where an error would otherwise travel
downstream silently:

| Where | Check | Why there |
|---|---|---|
| `ingest` | every file claimed by exactly one adapter; every printed line and amount parsed, or refuse; unknown JSON keys, CSV columns and header lines refuse | a charge the parser silently skips can never be disputed later |
| `ingest` | carrier-side inconsistencies (total ≠ lines, components ≠ line total, duplicates, dangling credit notes) are recorded as anomalies, not failures | these are findings about the carrier, and must reach the report |
| `match` | a printed fact with no comparison rule is a hard failure | a new invoice field means the system has a gap, not that the data is fine |
| `contract-spec` | worker self-check (`speccheck`: schema, dangling references, tier overlaps, prices every real shipment); flowstate schema at `finish`; **two independent extractions must price about 180-250 probe shipments identically**, or a third breaks the tie | a misread clause is a wrong number on every line for that carrier; a behavioural diff catches misreadings that look similar as text (e.g. `lt 50` vs `lte 50`) |
| `check_specs` | every planned carrier has a spec for the right carrier that passes speccheck on current shipments (cached specs included) | the join could otherwise complete with a branch missing |
| `price` | every line gets a disposition from a named rule, or exit 1 | no line may fall through |
| `assemble` | I1-I9 (see `reconlib/assemble.py`): schema, each line once, delta arithmetic, null ⇒ escalate, totals vs printed, summary re-adds, **no rupee disputed twice**, every anomaly and fact conflict recorded, adjudication within bounds | the report is the deliverable; nothing is written unless all of these hold |
| `memo-batch` | every item has a memo naming its invoice, reference and disposition, quoting its key amount and **no rupee figure absent from its brief**; one retry with feedback | stops a hallucinated or recomputed number from reaching a colleague who will quote it to a carrier |
| `publish` | memo set == non-accept items exactly; stale memos removed; sha256 manifest | the deliverable directory matches the report |

## Judgement calls in the reconciliation

Each is implemented as a named rule in `reconlib/price.py`, so every
disposition in the report can be traced to exactly one of them.

- **Facts come from BlueFin, price comes from the contract.** Lines are priced
  from the shipment record, not from the facts the carrier printed. Conflicts
  are recorded on the line.
- **Undetermined means escalate with null amounts** (`R_UNDETERMINED`): an
  out-of-card weight, a tier gap such as Alpine's exactly 50 kg (neither
  "under 50" nor "over 50"), a service the contract doesn't offer, or a ship
  date outside the term. We don't pick a band for the carrier.
- **Unmatched lines are escalated, expected null** (`R_UNMATCHED`), with the
  near-miss reference in the justification (e.g. SG-7969 is one edit from
  SG-7069, an unbilled, in-transit shipment). Sagar §5 makes them "not payable
  until reconciled", which is a hold for a human, not a price.
- **Duplicate billing:** the first occurrence is priced normally; a later line
  for the same shipment gets expected 0 and is disputed in full (`R_DUPLICATE`).
- **Charges the contract does not list:** disputed in full when the contract
  closes its schedule (Falcon §5: detention); escalated when it is silent
  (`R_SILENT_CHARGE`). Whether a schedule is closed is itself extracted and
  cross-checked in stage 2.
- **Underbilling is accepted and noted** (`R_UNDERBILLED`). BlueFin pays what
  was billed; nothing is claimed back.
- **Credit notes are priced against the line they correct.** The credit's
  expected amount is minus that line's overbilling. A full credit accepts
  both lines. A short credit disputes the remainder **once, on the credit
  line**, and accepts the original as corrected (Sagar §6 requires credits to
  cover the full corrected amount), so no rupee is counted twice. An excess
  credit is in BlueFin's favour and is accepted with a note.
- **Invoice-level volume adjustments** are evaluated against consignments
  **tendered** in the calendar month (from shipments.json), applied to the
  **contract** line total. If the carrier applied the right percentage to its
  own (partly disputed) line total, the small difference is derivative of the
  line disputes and is accepted rather than counted again
  (`R_ADJ_DERIVATIVE`). If lines on the invoice are undetermined, the
  adjustment on the determinable lines is a certain lower bound and is
  disputed on its own (`R_ADJ_SHORT`, e.g. ALPINE-0726 printed no discount
  on a 40-consignment month).

## Changes to the provided machinery

### flowstate: gates on a join's outgoing edge were skipped (`orchestrator/lib/flowstate/traversal.py`)

**Found:** in the first stage-2 run (`specs-try1`), a completeness gate on
`specs_join -> done` never ran. `_fire_join` activated the join's downstream
node directly, without the gate evaluation that `decide_next_action` performs
for every other transition. The downstream was the end node, and `_fire_join`
also never completed the run: it left `done` `in_progress`, and the next
`advance` raised `TypeError`. The run's event log records the abandonment
(`run_abandoned`).

**Why it matters here:** the brief's core promise is that the graph "can't
advance past failed validation." A gate that silently doesn't run breaks
that promise, and nothing surfaces the failure.

**Change:**
- New `_check_join_out_edge` runs the out-edge's gates against the
  **post-merge** variables, so a gate after a join can check what the
  branches produced. It runs before anything is committed. On failure the
  join stays `ready_to_fire`, a `join_blocked` event is logged, and
  `advance` returns `blocked`, so the next `advance` re-runs the gate:
  the same retry semantics as an ordinary gate.
- When the join's downstream is an end node (`Msquare`), the run is
  completed exactly as `_apply_decision` completes it (phase done,
  `run_completed` event, subflow completion push).
- Conditions are not handled: a join has one outgoing edge, and the parser
  already rejects conditions on a single outgoing edge.

**Verification:** `orchestrator/tests/test_join_out_edge.py` (new; flowstate
shipped without tests) drives script-only fork/join flows through the real
CLI in a throwaway repo. All three tests fail against a copy of flowstate
with the change reverted, and pass with it. The `smoke-branch` demo still
completes, and `specs-try2` ran `specs_join -> check_specs -> done` to
`completed` on the patched code.

### flowstate: 16 KB variable ceiling (no change; designed around)

Every flowstate variable is capped at 16 KB ("variables hold references,
not blobs"). The first design fanned out over lists of dicts and collected
results into dict variables; at 50× the memo batch list alone was 42 KB and
the run would have stopped at the fanout. Fanout items are now ids (carrier
keys; integer batch ids), each child's first node (`load_item`) resolves its
details from the plan file, the spec reducer writes `rate-specs.json` in the
run dir instead of a variable, and `publish` takes memo dirs from the memo
plan. `tests/test_flows.py` asserts the memo fanout list fits for 100,000
non-accept items (batch size grows past about 20,000 rather than the id list).

### flowstate: a dynamic_fanout's join without a reducer never fires (no change; designed around)

`subflow.py` marks a dynamic_fanout's join `ready_to_fire` only from inside
the join's reducer hook (`if reducer_node.reducer_script and
reducer_node.summary_var:`). A join without a reducer is never fired, and the
run stalls with every branch done (`recon-final`: `memos_join` blocked after
all three memo batches finished). `memos_join` now has a small reducer that
counts completed batches, and `tests/test_flows.py` requires a reducer on
every join fed by a dynamic_fanout. The right machinery fix is to move the
ready-to-fire flip out of the reducer branch; left for a human decision.

### agentctl: a worker cannot be sent validation feedback

The graph-orchestrator skill says a worker whose output fails validation "is
deliberately left alive (it may be able to fix its own output)". For the
claude-code harness it is not: the tmux wrapper (`harnesses/tmux_tui.py`)
kills the worker as soon as `completion.yml` appears, before validation runs.
`recon-final` showed it: feedback was sent to an already-dead worker.
`bin/drive-branch.sh` therefore retries by **respawning with the validator's
feedback appended to the prompt**, once, and logs `worker_respawned` /
`run_stopped` events in the child run.

## Post-implementation notes

- Kit arrived without execute bits (Windows download) and outside a git
  repo; flowstate requires both. `jq` is required by the SessionStart hook.
- Workers inherit the operator's user-level MCP servers (a GitHub MCP
  container starts per worker). TODO: decide whether to restrict worker
  settings.
- **Runs to date** (`factory/graph_runs/freight-recon/`): `specs-try1`
  (abandoned: join gate bug), `specs-try2`, `recon-20260927` (first full run),
  `recon-final` (stopped at memos on a schema bug of mine, resumed with the
  fixed driver, completed), and **`recon-submission`**: the clean run on the
  final code, with `use_spec_cache=no` so it extracts every contract itself.
  The submitted report and memos come from `recon-submission`. Every full run
  produced the same report summary (380 lines; 364 / 12 / 4; ₹16,618.06 in
  dispute).
- **Scale check (50×, synthetic: the sample cloned 50 times with remapped ids;
  850 documents, 19,000 lines, 19,100 shipments).** Bootstrapped through
  flowstate with cached specs: `plan_contracts → check_specs` (no extraction
  workers), then price, assemble and plan_memos. 17 s from bootstrap to an
  assembled report exactly 50× the sample (600 disputes, 200 escalations,
  ₹830,903.00 in dispute = 50 × ₹16,618.06). The memo fanout then registered
  all 107 batches, where the previous design failed on the 16 KB variable
  ceiling. Memo writing at that size is about 107 workers of about 1 min each,
  3 at a time (flowstate's default `max_concurrent`, raisable per flow in
  `factory/factory-prefs.yml`), so about 35 min. The run was stopped there and
  removed; the generator lives outside the repo. Intermediate JSON files reach
  about 30 MB at 50×; at 500×, sharding them per carrier would be the next step.
- **Resumability.** `drive-branch.sh` resumes a stopped child by respawning
  its unfinished agent node; `recon-final` was completed that way after the
  fix. When a join stalls, `flowstate complete-subflow --branch <id>` re-fires
  the completion hook.
- Contract extraction variance: across `specs-try1` and `specs-try2`, the
  two extractions of the Sagar contract disagreed once (on `closed_list`)
  and agreed once. The tie-break resolved try1 to the same value try2
  agreed on, so the final specs matched across runs on pricing and on
  `closed_list`. The declared ambiguities vary in wording and count between
  extractions without changing any price. Across five runs, one of the three
  contracts needed the tie-break in three of them, each time for a different
  reason: Sagar `closed_list` (a real reading difference); Falcon (an engine
  bug of mine: an undetermined derived quantity raised instead of returning
  undetermined, since fixed and tested); Sagar again (one extraction declared
  "fragile handling not covered" as an ambiguity, and the literal majority
  won). The final agreed specs priced every real line identically in every
  run.
