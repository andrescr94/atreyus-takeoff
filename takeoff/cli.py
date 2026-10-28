"""Command line entry point: one subcommand per pipeline stage, plus `run` for the whole thing.

    takeoff ingest plans/X.pdf          split the PDF into sheets (data/sheets/<id>/)
    takeoff legend A1-111               parse that sheet's legend into templates
    takeoff detect A1-111               detect devices (no rooms) and write the overlay
    takeoff rooms A1-111                find rooms and write the tinted overlay
    takeoff run A1-111                  detect + rooms + counts table
    takeoff eval vision-crop [--scan]   score one detection method against eval/answer_key (live; --replay re-scores)
    takeoff compare [--replay]          score every method and the one-shot agent runs -> docs/results.md
    takeoff key                         the answer key as a readable page -> docs/answer-key.md
    takeoff import plans/X.pdf          new project from a PDF, read like a web upload (data/takeoff.db)
    takeoff serve [--port 8000]         the review web app and its API on http://localhost:8000
"""
import sys

from takeoff import api, evaluate, ingest, jobs, legend, match, pipeline, rooms

COMMANDS = {
    "ingest": ingest.main,
    "legend": legend.main,
    "detect": match.main,
    "rooms": rooms.main,
    "run": pipeline.main,
    "eval": evaluate.main_eval,
    "compare": evaluate.main_compare,
    "key": evaluate.main_key,
    "import": jobs.main,
    "serve": api.main,
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main())
