"""Command line entry point: `python -m uarb_agent <command>`."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from .agent import RegulatoryAgent, make_claude_client, serve
from .config import Settings
from .models import DOCUMENT_TYPES
from .packager import build_zip
from .reply import compose_reply
from .request_parser import normalise_document_type, normalise_matter_number
from .scraper import MatterNotFoundError, UARBScraper


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="uarb_agent", description="Fetch UARB filings on request by email."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="watch the mailbox and answer requests")
    run.add_argument("--once", action="store_true", help="process unread mail once, then exit")

    fetch = sub.add_parser("fetch", help="download documents for a matter without email")
    fetch.add_argument("matter", help="matter number, e.g. M12205")
    fetch.add_argument("document_type", help=f"one of: {', '.join(DOCUMENT_TYPES)}")
    fetch.add_argument("--out", type=Path, default=Path("output"), help="output folder")
    fetch.add_argument("--limit", type=int, help="max documents (default: MAX_DOCUMENTS)")

    ask = sub.add_parser("ask", help="answer a plain-text request as if it had been emailed")
    ask.add_argument("text", help='e.g. "Can you give me Other Documents files from M12205?"')
    ask.add_argument("--name", default="", help="sender name for the greeting")
    ask.add_argument("--out", type=Path, default=Path("output"), help="output folder")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    if args.headed:
        settings = replace(settings, headless=False)

    if args.command == "run":
        serve(settings, once=args.once)
    elif args.command == "fetch":
        _fetch(settings, args)
    elif args.command == "ask":
        _ask(settings, args)


def _fetch(settings: Settings, args: argparse.Namespace) -> None:
    matter = normalise_matter_number(args.matter)
    doc_type = normalise_document_type(args.document_type)
    if not matter or not doc_type:
        sys.exit(f"Need a matter number like M12205 and one of: {', '.join(DOCUMENT_TYPES)}")
    limit = args.limit if args.limit is not None else settings.max_documents
    out = args.out.resolve()
    try:
        with UARBScraper(headless=settings.headless) as scraper:
            result = scraper.fetch(
                matter,
                doc_type,
                out / f"{matter}_files",
                limit=limit,
                max_bytes=settings.max_attachment_bytes,
            )
    except MatterNotFoundError:
        sys.exit(f"The UARB site has no matter {matter}")
    package = build_zip(
        result.documents, out / f"{matter}_{doc_type.replace(' ', '_')}.zip", settings.max_attachment_bytes
    )
    print(compose_reply(result, package, "there", settings.max_attachment_mb))
    if package.path:
        print(f"ZIP: {package.path}")


def _ask(settings: Settings, args: argparse.Namespace) -> None:
    agent = RegulatoryAgent(settings, make_claude_client())
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uarb-agent-") as tmp:
        reply = agent.handle(subject="", body=args.text, sender_name=args.name, work_dir=Path(tmp))
        print(reply.body)
        if reply.attachment:
            target = out / reply.attachment.name
            shutil.copy2(reply.attachment, target)
            print(f"ZIP: {target}")


if __name__ == "__main__":
    main()
