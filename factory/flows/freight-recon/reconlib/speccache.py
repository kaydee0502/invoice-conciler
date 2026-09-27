"""Cache of agreed rate specs, keyed by what determines an extraction.

key = sha256(carrier, contract bytes, rate-spec schema bytes, extraction prompt bytes)

An unchanged contract is extracted once and reused on later runs; editing the
contract, the spec language, or the extraction prompt changes the key, so a
stale spec can never be served. A cached spec is still re-verified by
check_specs against the current shipments before anything is priced with it.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

FLOW_DIR = Path(__file__).resolve().parents[1]
SCHEMA = FLOW_DIR / "definitions" / "rate-spec.json"
PROMPT = FLOW_DIR.parent / "contract-spec" / "prompts" / "extract_a.md"


def key(carrier: str, contract_path: Path) -> str:
    h = hashlib.sha256()
    for part in (carrier.encode(), contract_path.read_bytes(), SCHEMA.read_bytes(), PROMPT.read_bytes()):
        h.update(hashlib.sha256(part).digest())
    return h.hexdigest()


def path_for(cache_dir: Path, carrier: str, k: str) -> Path:
    return cache_dir / f"{carrier}-{k[:20]}.json"


def lookup(cache_dir: Path, carrier: str, contract_path: Path) -> Path | None:
    p = path_for(cache_dir, carrier, key(carrier, contract_path))
    return p if p.is_file() else None


def store(cache_dir: Path, carrier: str, contract_path: Path, spec_path: Path, source_run: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    k = key(carrier, contract_path)
    spec = json.loads(spec_path.read_text())
    spec.setdefault("_provenance", {})["cache"] = {"key": k, "source_run": source_run}
    dest = path_for(cache_dir, carrier, k)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(dest)
    return dest


def copy_into_run(cached: Path, run_dir: Path, carrier: str) -> Path:
    dest = run_dir / "cached-specs" / f"{carrier}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cached, dest)
    return dest
