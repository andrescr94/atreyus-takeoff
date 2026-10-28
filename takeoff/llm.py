"""One door to Claude, two ways to open it, and a cache so a replay costs nothing.

    TAKEOFF_LLM=cli  (default)  `claude -p` signed in with a Claude subscription (the demo)
    TAKEOFF_LLM=api             Anthropic Messages API with ANTHROPIC_API_KEY (a customer's own key)
    TAKEOFF_MODEL               model alias for the cli (default "opus") or model id for the api
                                (default "claude-opus-5-5")

Every call goes to the model live. Every answer is also saved to data/answers/<hash>.json, keyed by the
inputs (model, prompt, image bytes, run number), as the record behind each reported number.
`takeoff eval --replay` (or TAKEOFF_REPLAY=1) re-scores those saved answers instead of calling the model:
that is for re-checking a scoreboard, never for a demo. `run` > 0 asks again on purpose (a second opinion
for the consensus method).

Answers are structured: with `schema` (a JSON Schema) the CLI enforces it (`--json-schema`) and the API forces a
tool call with that input schema; the parsed object is Answer.data. No structured answer -> RuntimeError, never a
silent empty result. Token counts are recorded per call (input includes cached prompt tokens; on the CLI that
includes Claude Code's own system prompt, about 46k tokens per call).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ANSWERS_DIR = Path("data/answers")
REPLAY = os.environ.get("TAKEOFF_REPLAY") == "1"
CLI_TIMEOUT_S = 600
class _Ledger(threading.local):
    """Every Answer returned in this thread since the caller last cleared it (takeoff.evaluate sums time and
    tokens). Per thread, so sheets can run in parallel; a method that fans out to threads adds their answers."""

    def __init__(self) -> None:
        self.items: list = []

    def append(self, a) -> None:
        self.items.append(a)

    def extend(self, answers) -> None:
        self.items.extend(answers)

    def clear(self) -> None:
        self.items.clear()

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __eq__(self, other) -> bool:
        return list(self.items) == list(other)


LEDGER = _Ledger()


@dataclass
class Answer:
    text: str
    model: str
    backend: str
    seconds: float
    cost_usd: float | None  # what the call would cost on the API; on a subscription it uses plan limits
    replayed: bool = False  # True when read back from data/answers instead of asking the model
    input_tokens: int | None = None  # prompt + images, cached parts included
    output_tokens: int | None = None
    data: dict | list | None = None  # the structured answer when a schema was given


def backend() -> str:
    return os.environ.get("TAKEOFF_LLM", "cli")


def model_name() -> str:
    default = "opus" if backend() == "cli" else "claude-opus-5-5"
    return os.environ.get("TAKEOFF_MODEL", default)


def _key(prompt: str, images: list[Path], model: str, run: int, schema: dict | None = None) -> str:
    h = hashlib.sha256()
    for part in (model, prompt, str(run)) + ((json.dumps(schema, sort_keys=True),) if schema else ()):
        h.update(part.encode())
    for img in images:
        h.update(Path(img).read_bytes())
    return h.hexdigest()[:24]


def ask(prompt: str, images: list[Path], run: int = 0, schema: dict | None = None) -> Answer:
    """Send the prompt with the images (PNG files); with a schema, Answer.data is the validated object."""
    model = model_name()
    path = ANSWERS_DIR / f"{_key(prompt, images, model, run, schema)}.json"
    if REPLAY and path.exists():
        answer = Answer(**{**json.loads(path.read_text()), "replayed": True})
    else:
        t0 = time.time()
        if backend() == "cli":
            answer = _ask_cli(prompt, images, model, schema)
        else:
            answer = _ask_api(prompt, images, model, schema)
        if schema is not None and answer.data is None:
            raise RuntimeError(f"model returned no structured answer: {answer.text[:300]!r}")
        answer.seconds = round(time.time() - t0, 1)
        ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(answer), indent=1))
    LEDGER.append(answer)
    return answer


def _ask_cli(prompt: str, images: list[Path], model: str, schema: dict | None = None) -> Answer:
    files = "\n".join(f"- {Path(p).resolve()}" for p in images)
    full = f"Read these images with the Read tool, in this order:\n{files}\n\n{prompt}"
    cmd = ["claude", "-p", full, "--model", model, "--allowedTools", "Read", "--output-format", "json"]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {proc.stdout[-400:]} {proc.stderr[-400:]}")
    out = json.loads(proc.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude -p error: {out.get('result')}")
    used = ",".join(out.get("modelUsage", {}).keys()) or model
    u = out.get("usage", {})
    return Answer(text=out["result"], model=used, backend="cli", seconds=0.0, cost_usd=out.get("total_cost_usd"),
                  input_tokens=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                  + u.get("cache_creation_input_tokens", 0),
                  output_tokens=u.get("output_tokens"), data=out.get("structured_output"))


def _ask_api(prompt: str, images: list[Path], model: str, schema: dict | None = None) -> Answer:
    import anthropic  # only needed for this backend

    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": base64.b64encode(Path(p).read_bytes()).decode()}}
        for p in images
    ]
    content.append({"type": "text", "text": prompt})
    extra = {}
    if schema is not None:  # a forced tool call is how the API returns an object that matches a schema
        extra = {"tools": [{"name": "answer", "description": "Return the answer.", "input_schema": schema}],
                 "tool_choice": {"type": "tool", "name": "answer"}}
    msg = anthropic.Anthropic().messages.create(
        model=model, max_tokens=16000, messages=[{"role": "user", "content": content}], **extra
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    data = next((b.input for b in msg.content if b.type == "tool_use"), None)
    return Answer(text=text or json.dumps(data), model=msg.model, backend="api", seconds=0.0, cost_usd=None,
                  input_tokens=msg.usage.input_tokens, output_tokens=msg.usage.output_tokens, data=data)


def json_array(text: str) -> list[dict]:
    """The first [...] block in a model answer (models sometimes wrap JSON in a code fence)."""
    return json.loads(text[text.index("["): text.rindex("]") + 1])
