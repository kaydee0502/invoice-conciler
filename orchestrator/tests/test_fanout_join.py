"""Regression tests: the join after a dynamic_fanout fires with or without a reducer.

Also covers the reducer environment: an accumulating reducer must see the
accumulated summary_var on every arrival, not each branch's stale snapshot.

Before the fix, subflow.complete_subflow only flipped the fanout's downstream
join to ready_to_fire inside the reducer branch, so a join without
reducer_script/summary_var was never fired: the parent blocked at the join
forever with every child done.

Each test builds a parent flow (dynamic_fanout over two items -> subflow
template -> join -> end) and a script-only child flow in a throwaway git
repo, and drives them through the real CLI; no agents run.

Run: orchestrator/.venv/bin/python -m unittest discover -s orchestrator/tests
"""
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

FLOWSTATE = Path(__file__).resolve().parents[1] / "bin" / "flowstate"

CHILD_DOT = textwrap.dedent("""\
    digraph c {
      start [shape=Mdiamond]
      work  [shape=box, runner=script, script="scripts/work.sh", working_dir="{_run_artefact_dir}", output_schema="w"]
      done  [shape=Msquare]
      start -> work
      work -> done
    }
    """)
CHILD_YML = textwrap.dedent("""\
    output_schemas:
      w:
        files: [{ name: out, path: "{_run_artefact_dir}/out.json", definition: out }]
        sets_variables: { out: out }
    variables:
      out: { type: path }
    """)
WORK = textwrap.dedent("""\
    #!/usr/bin/env bash
    set -euo pipefail
    printf '{"_session_id": "w", "ok": true}\\n' > "${FLOWSTATE_VAR_out:?}"
    """)
REDUCER = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os
    n = int(json.loads(os.environ.get("FLOWSTATE_VAR_arrivals") or "0"))
    print(f"FLOWSTATE_OUTPUT_arrivals={n + 1}")
    """)


def parent_dot(join_attrs: str) -> str:
    return textwrap.dedent(f"""\
        digraph p {{
          start [shape=Mdiamond]
          fan   [runner=dynamic_fanout, source="items", template="br"]
          br    [runner=subflow, flow="c"]
          j     [runner=join{join_attrs}]
          done  [shape=Msquare]
          start -> fan
          fan -> br
          br -> j
          j -> done
        }}
        """)


PARENT_YML = textwrap.dedent("""\
    output_schemas: {}
    variables:
      items: { type: list, items: string }
      arrivals: { type: string, default: "0" }
    """)


class FanoutJoinTest(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        (self.repo / "factory").mkdir()
        (self.repo / "factory" / "factory-prefs-example.yml").write_text("supervision: afk\ndefault_graph: p\n")
        c = self.repo / "factory" / "flows" / "c"
        (c / "scripts").mkdir(parents=True)
        (c / "definitions").mkdir()
        (c / "c.dot").write_text(CHILD_DOT)
        (c / "c.flow.yml").write_text(CHILD_YML)
        (c / "definitions" / "out.json").write_text('{"type": "object", "required": ["ok"]}')
        w = c / "scripts" / "work.sh"
        w.write_text(WORK)
        w.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.repo)

    def make_parent(self, with_reducer: bool) -> Path:
        p = self.repo / "factory" / "flows" / "p"
        (p / "scripts").mkdir(parents=True)
        attrs = ', reducer_script="scripts/reduce.py", summary_var="arrivals"' if with_reducer else ""
        (p / "p.dot").write_text(parent_dot(attrs))
        (p / "p.flow.yml").write_text(PARENT_YML)
        r = p / "scripts" / "reduce.py"
        r.write_text(REDUCER)
        r.chmod(0o755)
        return p / "p.dot"

    def fs(self, *args) -> dict:
        r = subprocess.run([str(FLOWSTATE), *args], cwd=self.repo, capture_output=True, text=True,
                           env={**os.environ, "FACTORY_ROOT": str(self.repo)})
        out = yaml.safe_load(r.stdout)
        self.assertIsInstance(out, dict, r.stdout + r.stderr)
        return out

    def run_fanout(self, with_reducer: bool) -> tuple[Path, list[str]]:
        env = self.fs("bootstrap", "--flow-dot", str(self.make_parent(with_reducer)), "--run-descriptor", "t",
                      "--orchestrator-session-id", "test", "--supervision", "afk",
                      "--seed-var-json", "items=" + json.dumps(["a", "b"]))
        self.assertEqual(env["status"], "ok", env)
        run_dir = Path(env["payload"]["resolved"]["run_dir"])
        adv = self.fs("advance", "--run-dir", str(run_dir))["payload"]
        branches = adv["next_startable_branches"]
        self.assertEqual(len(branches), 2, adv)
        for b in branches:
            child = self.fs("start-branch", "--run-dir", str(run_dir), "--node", "fan", "--branch", b)
            child_dir = child["payload"]["subflow_run_dir"]
            # The child is script-only: one advance runs it to its end node, and
            # its completion pushes to the parent (harvest, reducer, join flip).
            self.assertEqual(self.fs("advance", "--run-dir", child_dir)["payload"]["kind"], "end")
        return run_dir, branches

    def drive_parent_to_end(self, run_dir: Path) -> str:
        kind = None
        for _ in range(4):
            adv = self.fs("advance", "--run-dir", str(run_dir))["payload"]
            kind = adv["kind"]
            if kind != "moved":
                break
        return kind

    def state(self, run_dir: Path) -> dict:
        return yaml.safe_load((run_dir / "graph_run_state.yml").read_text())

    def test_join_without_reducer_fires(self):
        run_dir, _ = self.run_fanout(with_reducer=False)
        self.assertEqual(self.drive_parent_to_end(run_dir), "end")
        self.assertEqual(self.state(run_dir)["metadata"]["state"], "completed")

    def test_join_with_reducer_still_fires_and_reduces(self):
        run_dir, _ = self.run_fanout(with_reducer=True)
        self.assertEqual(self.drive_parent_to_end(run_dir), "end")
        st = self.state(run_dir)
        self.assertEqual(st["metadata"]["state"], "completed")
        # Every arrival must see the accumulated value, not the branch's stale
        # snapshot of it (regression: a counting reducer ended at 1, not 2).
        self.assertEqual(str(self.fs("vars", "--run-dir", str(run_dir))["payload"]["arrivals"]), "2")

    def test_repeated_completion_push_does_not_rearm_fired_join(self):
        run_dir, branches = self.run_fanout(with_reducer=False)
        self.assertEqual(self.drive_parent_to_end(run_dir), "end")
        before = [e for e in self.state(run_dir)["events"] if e["kind"] == "join_ready_to_fire_after_fanout"]
        self.assertEqual(self.fs("complete-subflow", "--branch", branches[0], str(run_dir))["status"], "ok")
        st = self.state(run_dir)
        after = [e for e in st["events"] if e["kind"] == "join_ready_to_fire_after_fanout"]
        self.assertEqual(len(after), len(before))
        self.assertEqual(st["current"]["nodes"]["j"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
