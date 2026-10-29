"""Ties it together: email in -> parse -> scrape -> ZIP -> email out."""

from __future__ import annotations

import imaplib
import logging
import os
import re
import smtplib
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic
from playwright.sync_api import Error as PlaywrightError

from .config import Settings
from .mailer import IncomingEmail, Mailbox
from .packager import build_zip
from .reply import (
    compose_clarification,
    compose_error,
    compose_not_found,
    compose_reply,
    greeting_name,
)
from .request_parser import parse_request
from .scraper import MatterNotFoundError, ScraperError, UARBScraper

log = logging.getLogger(__name__)


@dataclass
class Reply:
    body: str
    attachment: Path | None = None


def make_claude_client() -> anthropic.Anthropic | None:
    """A Claude client if credentials are configured, else None (rule-based parsing)."""
    if any(os.getenv(v) for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")):
        return anthropic.Anthropic()
    log.warning("No Anthropic credentials found; requests will be parsed with rules only")
    return None


class RegulatoryAgent:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self.settings = settings
        self.client = client

    def handle(self, subject: str, body: str, sender_name: str, work_dir: Path) -> Reply:
        """Answer one request. Files are written under `work_dir`."""
        name = greeting_name(sender_name)
        request = parse_request(f"Subject: {subject}\n\n{body}", self.client, self.settings.anthropic_model)
        if not request.matter_number or not request.document_type:
            return Reply(compose_clarification(request, name))

        matter, doc_type = request.matter_number, request.document_type
        log.info("Fetching %s from %s", doc_type, matter)
        try:
            with UARBScraper(headless=self.settings.headless) as scraper:
                result = scraper.fetch(
                    matter,
                    doc_type,
                    work_dir / "files",
                    limit=self.settings.max_documents,
                    max_bytes=self.settings.max_attachment_bytes,
                )
        except MatterNotFoundError:
            return Reply(compose_not_found(matter, name))
        except (ScraperError, PlaywrightError):
            log.exception("Scraping %s failed", matter)
            return Reply(compose_error(name, matter))

        slug = re.sub(r"\W+", "_", doc_type)
        package = build_zip(
            result.documents, work_dir / f"{matter}_{slug}.zip", self.settings.max_attachment_bytes
        )
        body = compose_reply(result, package, name, self.settings.max_attachment_mb)
        return Reply(body, package.path)


def serve(settings: Settings, once: bool = False) -> None:
    """Poll the mailbox and answer each new request."""
    missing = settings.missing_email_settings()
    if missing:
        raise SystemExit(f"Missing email settings: {', '.join(missing)} (see .env.example)")
    mailbox = Mailbox(settings)
    agent = RegulatoryAgent(settings, make_claude_client())
    log.info("Watching %s every %ds", settings.email_address, settings.poll_interval)
    while True:
        try:
            emails = mailbox.fetch_unread()
        except (imaplib.IMAP4.error, OSError) as exc:
            log.error("Could not check the mailbox: %s", exc)
            emails = []
        for mail in emails:
            _answer(mail, agent, mailbox, settings)
        if once:
            return
        time.sleep(settings.poll_interval)


def _answer(mail: IncomingEmail, agent: RegulatoryAgent, mailbox: Mailbox, settings: Settings) -> None:
    if mail.automated:
        log.info("Ignoring automated message from %s", mail.sender)
        return
    if not settings.is_allowed_sender(mail.sender):
        log.info("Ignoring message from %s (not in ALLOWED_SENDERS)", mail.sender)
        return
    log.info("Request from %s: %r", mail.sender, mail.subject)
    with tempfile.TemporaryDirectory(prefix="uarb-agent-") as tmp:
        try:
            reply = agent.handle(mail.subject, mail.body, mail.sender_name, Path(tmp))
        except Exception:
            log.exception("Unexpected error while handling a request from %s", mail.sender)
            reply = Reply(compose_error(greeting_name(mail.sender_name)))
        try:
            mailbox.send_reply(mail, reply.body, reply.attachment)
        except (smtplib.SMTPException, OSError):
            log.exception("Could not send the reply to %s", mail.sender)
