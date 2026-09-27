"""Regression tests: a join's outgoing edge is a real transition.

Before the fix, flowstate's _fire_join activated the join's downstream
without running the edge's gates (a failing gate was silently skipped), and a join whose downstream was the end node left the run
stuck with the end node in_progress.

Each test builds a tiny script-only flow (fork -> a, b -> join -> end) in a
throwaway git repo and drives it through the real CLI, so no agents run.

Run: orchestrator/.venv/bin/python -m unittest discover -s orchestrator/tests
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

FLOWSTATE = Path(__file__).resolve().parents[1] / "bin" / "flowstate"

ARM = textwrap.dedent("""\
    #!/usr/bin/env bash
    set -euo pipefail
    printf '{"_session_id": "arm", "note": "%s"}\\n' "$FLOWSTATE_PHASE" > "${FLOWSTATE_VAR_NOTEVAR:?}"
    """)

GATE = textwrap.dedent("""\
    #!/usr/bin/env bash
    # Passes only when the repo has an 'allow' file AND the merged branch vars are visible.
    set -euo pipefail
    [ -n "${FLOWSTATE_VAR_note_a:-}" ] && [ -n "${FLOWSTATE_VAR_note_b:-}" ] || { echo "branch vars not visible to gate" >&2; exit 1; }
    [ -f "$FACTORY_ROOT/allow" ] || { echo "not allowed yet" >&2; exit 1; }
    """)


def dot(edge_attrs: str) -> str:
    return textwrap.dedent(f"""\
        digraph t {{
          start [shape=Mdiamond]
          fork  [runner=fork]
          a     [shape=box, runner=script, script="scripts/a.sh", working_dir="{{_run_artefact_dir}}", output_schema="a_out"]
          b     [shape=box, runner=script, script="scripts/b.sh", working_dir="{{_run_artefact_dir}}", output_schema="b_out"]
          j     [runner=join]
          done  [shape=Msquare]
          start -> fork
          fork -> a
          fork -> b
          a -> j
          b -> j
          j -> done {edge_attrs}
        }}
        """)


FLOW_YML = textwrap.dedent("""\
    output_schemas:
      a_out:
        files: [{ name: note_a, path: "{_run_artefact_dir}/a.json", definition: note }]
        sets_variables: { note_a: note_a }
      b_out:
        files: [{ name: note_b, path: "{_run_artefact_dir}/b.json", definition: note }]
        sets_variables: { note_b: note_b }
    variables:
      note_a: { type: path }
      note_b: { type: path }
    """)


class JoinOutEdgeTest(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)

    def tearDown(self):
        shutil.rmtree(self.repo)

    def make_flow(self, edge_attrs: str) -> Path:
        d = self.repo / "factory" / "flows" / "t"
        (d / "scripts").mkdir(parents=True)
        (self.repo / "factory" / "factory-prefs-example.yml").write_text("supervision: afk\ndefault_graph: t\n")
        (d / "gates").mkdir()
        (d / "definitions").mkdir()
        (d / "t.dot").write_text(dot(edge_attrs))
        (d / "t.flow.yml").write_text(FLOW_YML)
        (d / "definitions" / "note.json").write_text(
            '{"type": "object", "required": ["note"], "properties": {"note": {"type": "string"}}}')
        for arm, var in (("a", "note_a"), ("b", "note_b")):
            p = d / "scripts" / f"{arm}.sh"
            p.write_text(ARM.replace("NOTEVAR", var))
            p.chmod(0o755)
        g = d / "gates" / "check.sh"
        g.write_text(GATE)
        g.chmod(0o755)
        return d / "t.dot"

    def fs(self, *args) -> dict:
        r = subprocess.run([str(FLOWSTATE), *args], cwd=self.repo, capture_output=True, text=True,
                           env={**os.environ, "FACTORY_ROOT": str(self.repo)})
        return yaml.safe_load(r.stdout)

    def bootstrap(self, flow_dot: Path) -> tuple[dict, Path]:
        env = self.fs("bootstrap", "--flow-dot", str(flow_dot), "--run-descriptor", "t",
                      "--orchestrator-session-id", "test-session", "--supervision", "afk")
        self.assertEqual(env["status"], "ok", env)
        return env["payload"]["advance"], Path(env["payload"]["resolved"]["run_dir"])

    def run_state(self, run_dir: Path) -> dict:
        return yaml.safe_load((run_dir / "graph_run_state.yml").read_text())

    def test_failing_gate_blocks_and_join_stays_retryable(self):
        adv, run_dir = self.bootstrap(self.make_flow('[gates="gates/check.sh"]'))
        self.assertEqual(adv["kind"], "blocked", adv)
        self.assertIn("not allowed yet", adv.get("reason", ""))
        st = self.run_state(run_dir)
        self.assertNotEqual(st["metadata"].get("state"), "completed")
        self.assertIn("join_blocked", [e["kind"] for e in st["events"]])

        (self.repo / "allow").write_text("")
        adv = self.fs("advance", "--run-dir", str(run_dir))["payload"]
        self.assertEqual(adv["kind"], "end", adv)
        self.assertEqual(self.run_state(run_dir)["metadata"]["state"], "completed")

    def test_gate_sees_merged_branch_variables(self):
        (self.repo / "allow").write_text("")
        adv, run_dir = self.bootstrap(self.make_flow('[gates="gates/check.sh"]'))
        self.assertEqual(adv["kind"], "end", adv)  # the gate checks note_a/note_b are visible

    def test_join_into_end_node_completes_run(self):
        adv, run_dir = self.bootstrap(self.make_flow(""))
        self.assertEqual(adv["kind"], "end", adv)
        st = self.run_state(run_dir)
        self.assertEqual(st["metadata"]["state"], "completed")
        self.assertEqual(st["current"]["nodes"]["done"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
