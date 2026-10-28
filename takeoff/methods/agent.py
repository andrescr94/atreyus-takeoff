"""Rungs 7 and 8, agent: a coding agent works one sheet the way a person would, and a second agent checks it.

Rung 0 (eval/oneshot/) showed that a coding agent handed the PDF and the brief beats a single model call: it
zooms into the plan, reads each symbol against the legend, draws its marks back on the page and looks again.
This packages that loop as a detection method with a fixed prompt and a schema-checked answer, one sheet at a
time, so its output goes through the same rooms, review flags, store and scoreboard as every other method.

    agent       Claude Code (`claude -p`, sandboxed Bash, Python with pymupdf/opencv/OCR) on the sheet
    agent-pair  agent + Codex (`codex exec`, workspace sandbox) on the same sheet, merged: where the two vendors
                agree is high confidence, where they disagree goes to the top of the review queue

The agent works in a temporary folder holding only the sheet PDF and a link to the Python. Positions come back
in PDF points of the page. A sheet with no electrical plan or no legend is answered with status
"not_an_electrical_plan" and raises, so a stray page fails loudly instead of returning zero devices.
Each answer is saved in data/answers (replayable with --replay); transcripts in data/agent_runs/.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from takeoff import llm, policy
from takeoff.ingest import Sheet
from takeoff.match import Detection
from takeoff.methods import ELECTRICAL_SLUGS, Method, make_detection
from takeoff.methods.vision import merge

PROMPT_VERSION = 1
TIMEOUT_S = 30 * 60
ENV = Path("data/agent_env")  # Python for the agent: `uv venv data/agent_env` + pymupdf opencv numpy pillow rapidocr
RUNS = Path("data/agent_runs")
CLAUDE_MODEL = "opus"
CODEX_MODEL = "gpt-6.1-sol"
TYPES = [s for s in ELECTRICAL_SLUGS if s not in {"light", "fan", "detector"}] + ["other"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "problem", "devices"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "not_an_electrical_plan"]},
        "problem": {"type": "string", "description": "why the sheet cannot be taken off; empty when ok"},
        "devices": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "x", "y", "room", "confidence", "note"],
            "properties": {
                "type": {"type": "string", "enum": TYPES},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "room": {"type": "string"},
                "confidence": {"type": "number"},
                "note": {"type": "string", "description": "for type other: what the legend calls it; else empty"},
            },
        }},
    },
}

PROMPT = f"""[auto] You are doing an electrical takeoff of one drawing sheet. Work alone and use only the files in \
this folder. `sheet.pdf` is one page of a construction plan set. A Python with pymupdf, opencv, numpy, pillow and \
rapidocr is at ./env/bin/python; use any approach you like.

If the page has no electrical plan, or no symbol legend for it, answer status "not_an_electrical_plan" and say what \
you found in problem. Do not guess.

Otherwise find every electrical device symbol (lights, fans, switches, receptacles, detectors, junction boxes) drawn \
on the electrical plan. Use the sheet's legend to decide each symbol's type: match the symbol's shape to a legend \
row, then the letters printed next to it, which the legend defines; a legend row whose symbol carries letters applies \
only when those letters are printed next to the plan symbol. The legend's own sample symbols, notes, title blocks, \
other drawings on the sheet, and plumbing or mechanical symbols are not devices.
Types: {", ".join(TYPES)}. Use "other" for an electrical symbol none of them fits and put the legend's name for it \
in note.
For each device give its centre in PDF points of this page (origin top-left, the coordinates PyMuPDF uses), the room \
label printed on the plan that it sits in (outside the building: Exterior) and your confidence 0..1.
Before answering, draw your marks on a render of the plan and look at it: fix anything missed, doubled or off.

{policy.text()}"""


def _key(agent: str, model: str, sheet: Sheet, run: int) -> str:
    h = hashlib.sha256()
    for part in (agent, model, PROMPT, json.dumps(SCHEMA), str(run)):
        h.update(part.encode())
    h.update(Path(sheet.pdf_path).read_bytes())
    return h.hexdigest()[:24]


def _workdir(sheet: Sheet) -> Path:
    if not (ENV / "bin" / "python").exists():
        raise RuntimeError(f"agent Python missing: create it with `uv venv {ENV}` and install pymupdf "
                           "opencv-python-headless numpy pillow rapidocr-onnxruntime into it")
    work = Path(tempfile.mkdtemp(prefix=f"takeoff-{sheet.id}-"))
    shutil.copy(sheet.pdf_path, work / "sheet.pdf")
    (work / "env").symlink_to(ENV.resolve())
    return work


def _claude(work: Path, model: str) -> tuple[dict, int, int, str]:
    sandbox = {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False}}
    proc = subprocess.run(
        ["claude", "-p", PROMPT, "--model", model, "--output-format", "stream-json", "--verbose",
         "--json-schema", json.dumps(SCHEMA), "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
         "--settings", json.dumps(sandbox)],
        cwd=work, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    if proc.returncode != 0 or result.get("is_error") or not result.get("structured_output"):
        raise RuntimeError(f"claude agent failed ({proc.returncode}): {str(result.get('result'))[:300]} "
                           f"{proc.stderr[-300:]}")
    u = result.get("usage", {})
    tin = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
    return result["structured_output"], tin, u.get("output_tokens", 0), proc.stdout


def _codex(work: Path, model: str) -> tuple[dict, int, int, str]:
    (work / "schema.json").write_text(json.dumps(SCHEMA))
    proc = subprocess.run(
        ["codex", "exec", "-m", model, "-c", "mcp_servers={}", "-s", "workspace-write", "-C", str(work), "--skip-git-repo-check", "--json",
         "--output-schema", str(work / "schema.json"), "-o", str(work / "answer.json"), PROMPT],
        cwd=work, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    try:
        data = json.loads((work / "answer.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        raise RuntimeError(f"codex agent failed ({proc.returncode}): {e}; {proc.stderr[-300:]}") from e
    turns = [json.loads(line) for line in proc.stdout.splitlines() if '"turn.completed"' in line]
    tin = sum(t.get("usage", {}).get("input_tokens", 0) for t in turns)
    tout = sum(t.get("usage", {}).get("output_tokens", 0) for t in turns)
    return data, tin, tout, proc.stdout


def ask(agent: str, sheet: Sheet, run: int = 0) -> tuple[dict, llm.Answer]:
    """One agent's answer for one sheet: (structured answer, Answer with time and tokens). Live unless --replay."""
    model = CLAUDE_MODEL if agent == "claude" else CODEX_MODEL
    path = llm.ANSWERS_DIR / f"{_key(agent, model, sheet, run)}.json"
    if llm.REPLAY and path.exists():
        a = llm.Answer(**{**json.loads(path.read_text()), "replayed": True})
    else:
        work = _workdir(sheet)
        t0 = time.time()
        try:
            data, tin, tout, transcript = (_claude if agent == "claude" else _codex)(work, model)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        a = llm.Answer(text=json.dumps(data), model=f"{agent}:{model}", backend=agent,
                       seconds=round(time.time() - t0, 1), cost_usd=None, input_tokens=tin, output_tokens=tout,
                       data=data)
        RUNS.mkdir(parents=True, exist_ok=True)
        (RUNS / f"{sheet.id}-{Path(sheet.pdf_path).stem}-{agent}-{path.stem}.jsonl").write_text(transcript)
        llm.ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: v for k, v in a.__dict__.items() if k != "replayed"}, indent=1))
    if a.data.get("status") != "ok":
        raise ValueError(f"{sheet.id}: {agent} says this is not an electrical plan sheet: {a.data.get('problem')}")
    return a.data, a


def placed(data: dict) -> list[dict]:
    """The agent's devices in the shape vision.merge pairs (slug, cx, cy, item, confidence)."""
    out = []
    for item in data["devices"]:
        slug = item["type"] if item["type"] in ELECTRICAL_SLUGS else "other"
        ev = {"raw_type": item.get("note") or item["type"]} if slug == "other" else {}
        out.append({"slug": slug, "cx": float(item["x"]), "cy": float(item["y"]), "item": item,
                    "confidence": min(1.0, max(0.0, float(item["confidence"]))), "evidence": ev})
    return out


def _detections(sheet: Sheet, method: str, found: list[dict], model: str) -> list[Detection]:
    return [make_detection(sheet, n, d["slug"], d["cx"], d["cy"], d["confidence"], method=method,
                           room=d["item"].get("room"), model=model, **d.get("evidence", {}))
            for n, d in enumerate(found, 1)]


def detect_agent(sheet: Sheet) -> list[Detection]:
    data, a = ask("claude", sheet)
    llm.LEDGER.append(a)
    return _detections(sheet, "agent", placed(data), a.model)


def detect_pair(sheet: Sheet) -> list[Detection]:
    """Both agents on the sheet. If one fails, the other's answer stands alone, each device marked with why
    (a single read: lower confidence in review). If both fail, or one says it is not a plan, it raises."""
    def attempt(agent: str):
        try:
            return ask(agent, sheet)
        except RuntimeError as e:  # a crashed run, not a verdict on the sheet (ValueError) which must propagate
            return e

    with ThreadPoolExecutor(2) as pool:
        r0, r1 = pool.map(attempt, ("claude", "codex"))
    ok = [r for r in (r0, r1) if not isinstance(r, Exception)]
    if not ok:
        raise RuntimeError(f"{sheet.id}: both agents failed: {r0}; {r1}")
    llm.LEDGER.extend(a for _, a in ok)
    if len(ok) == 1:
        failed = next(r for r in (r0, r1) if isinstance(r, Exception))
        (d, a), = ok
        found = [{**x, "evidence": {**x["evidence"], "second_opinion": f"failed: {str(failed)[:120]}"}}
                 for x in placed(d)]
        return _detections(sheet, "agent-pair", found, a.model)
    (d0, a0), (d1, a1) = r0, r1
    return _detections(sheet, "agent-pair", merge(placed(d0), placed(d1)), f"{a0.model}+{a1.model}")


METHODS = {
    "agent": Method(
        name="agent", rung=7, needs="an image (works on vector or scan)",
        idea="a coding agent (Claude Code) works the sheet: zooms, reads symbols against the legend, draws its "
             "marks back and re-checks; schema-checked answer",
        detect=detect_agent,
    ),
    "agent-pair": Method(
        name="agent-pair", rung=8, needs="an image (works on vector or scan)",
        idea="the Claude agent and a Codex agent on the same sheet; agreement is the confidence, disagreements go "
             "first in review",
        detect=detect_pair,
    ),
}
