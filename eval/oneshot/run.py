"""One-shot baseline: hand a coding agent the PDF and the brief's requirements, nothing else, and keep what it returns.

    uv run python eval/oneshot/run.py claude opus  WORKDIR
    uv run python eval/oneshot/run.py codex gpt-6-astra WORKDIR
    uv run python eval/oneshot/run.py claude opus WORKDIR scan     same, on the scanned copy (eval scan pages)

WORKDIR must be outside this repo so the agent cannot find the answer key. It gets plans.pdf, prompt.md and an
`env` link to a Python with pymupdf/opencv/rapidocr. The agent's result.json, notes.md, full transcript and
meta.json (start, seconds, turns, tokens, cost) are copied to eval/oneshot/runs/<agent>-<model>/.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
PDF = Path(__file__).resolve().parents[2] / "plans/Escondido_Electrical_Plans_Ceiling_Fans.pdf"
TIMEOUT_S = 75 * 60


def command(agent: str, model: str, prompt: str, work: Path) -> list[str]:
    if agent == "claude":
        # Bash runs inside Claude Code's sandbox: writes only in the work folder, no network.
        return ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
                "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
                "--settings", json.dumps({"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True,
                                                      "allowUnsandboxedCommands": False}})]
    return ["codex", "exec", "-m", model, "-s", "workspace-write", "-C", str(work), "--skip-git-repo-check",
            "--json", "-o", str(work / "last_message.txt"), prompt]


def usage(agent: str, transcript: str) -> dict:
    events = [json.loads(line) for line in transcript.splitlines() if line.startswith("{")]
    if agent == "claude":
        r = next((e for e in reversed(events) if e.get("type") == "result"), {})
        u = r.get("usage", {})
        return {"turns": r.get("num_turns"), "cost_usd": r.get("total_cost_usd"),
                "input_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0),
                "output_tokens": u.get("output_tokens"), "models": list(r.get("modelUsage", {}))}
    turns = [e for e in events if e.get("type") == "turn.completed"]
    tools = [e for e in events if e.get("type") == "item.completed"
             and e.get("item", {}).get("type") == "command_execution"]
    return {"turns": len(tools), "cost_usd": None,
            "input_tokens": sum(t.get("usage", {}).get("input_tokens", 0) for t in turns),
            "output_tokens": sum(t.get("usage", {}).get("output_tokens", 0) for t in turns)}


def scan_pdf(out: Path) -> None:
    """The four sheets as the scoreboard scans them (takeoff.evaluate.scan_copy): images only, one PDF."""
    import pymupdf

    from takeoff.evaluate import SHEETS, scan_copy
    from takeoff.ingest import load_sheet

    doc = pymupdf.open()
    for sid in SHEETS:
        doc.insert_pdf(pymupdf.open(str(scan_copy(load_sheet(sid)).pdf_path)))
    doc.save(str(out))


def main(agent: str, model: str, base: str, variant: str = "") -> int:
    name = f"{agent}-{model}" + (f"-{variant}" if variant else "")
    work = Path(base) / name
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    if variant == "scan":
        scan_pdf(work / "plans.pdf")
    else:
        shutil.copy(PDF, work / "plans.pdf")
    (work / "env").symlink_to(Path(base) / "env")
    prompt = (HERE / "prompt.md").read_text()
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    t0 = time.time()
    try:
        proc = subprocess.run(command(agent, model, prompt, work), cwd=work, capture_output=True, text=True,
                              timeout=TIMEOUT_S, check=False)
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        out, err, code = (e.stdout or b"").decode(errors="replace"), "timeout", -1
    meta = {"agent": agent, "model": model, "variant": variant or "vector", "started": started, "seconds": round(time.time() - t0),
            "exit_code": code, "result_written": (work / "result.json").exists(), **usage(agent, out)}
    dest = HERE / "runs" / name
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for f in ("result.json", "notes.md", "last_message.txt"):
        if (work / f).exists():
            shutil.copy(work / f, dest / f)
    (dest / "transcript.jsonl").write_text(out)
    if err.strip():
        (dest / "stderr.txt").write_text(err[-20000:])
    (dest / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:5]))
