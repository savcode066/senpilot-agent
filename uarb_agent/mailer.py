"""Read requests over IMAP and send replies over SMTP."""

from __future__ import annotations

import html
import imaplib
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path

from .config import Settings
from .request_parser import parse_with_rules

log = logging.getLogger(__name__)

_AUTOMATED_SENDERS = re.compile(r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply)@", re.I)


@dataclass
class IncomingEmail:
    uid: str
    message_id: str
    sender: str
    sender_name: str
    subject: str
    body: str
    references: str = ""
    automated: bool = False


def parse_email(raw: bytes, uid: str = "") -> IncomingEmail:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    name, address = parseaddr(str(msg.get("From", "")))
    auto_submitted = str(msg.get("Auto-Submitted", "no")).lower()
    precedence = str(msg.get("Precedence", "")).lower()
    return IncomingEmail(
        uid=uid,
        message_id=str(msg.get("Message-ID", "")).strip(),
        sender=address,
        sender_name=name,
        subject=str(msg.get("Subject", "")).strip(),
        body=_body_text(msg),
        references=str(msg.get("References", "")).strip(),
        automated=(
            auto_submitted != "no"
            or precedence in {"bulk", "junk", "list"}
            or bool(_AUTOMATED_SENDERS.match(address))
        ),
    )


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = _html_to_text(text)
    # Drop quoted text from earlier messages in the thread.
    kept = []
    for line in text.splitlines():
        if re.match(r"^\s*On .+wrote:\s*$", line):
            break
        if not line.lstrip().startswith(">"):
            kept.append(line)
    return "\n".join(kept).strip()


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def build_reply(
    original: IncomingEmail,
    body: str,
    from_address: str,
    from_name: str,
    attachment: Path | None = None,
) -> EmailMessage:
    reply = EmailMessage()
    reply["From"] = formataddr((from_name, from_address))
    reply["To"] = formataddr((original.sender_name, original.sender))
    subject = original.subject or "Your UARB document request"
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    reply["Message-ID"] = make_msgid(domain=from_address.rpartition("@")[2] or None)
    if original.message_id:
        reply["In-Reply-To"] = original.message_id
        reply["References"] = f"{original.references} {original.message_id}".strip()
    # RFC 3834: marks this as an automatic reply so other robots don't answer it.
    reply["Auto-Submitted"] = "auto-replied"
    reply.set_content(body)
    if attachment is not None:
        reply.add_attachment(
            attachment.read_bytes(), maintype="application", subtype="zip", filename=attachment.name
        )
    return reply


def looks_like_request(mail: IncomingEmail, settings: Settings) -> bool:
    """Whether a message in the spam folder deserves an answer.

    Replying to spam would confirm the address to spammers, so a message has to
    come from an allowed sender and, unless ALLOWED_SENDERS narrows who that is,
    mention a matter number or document type.
    """
    if mail.automated or mail.sender.lower() == settings.email_address.lower():
        return False
    if not settings.is_allowed_sender(mail.sender):
        return False
    if settings.allowed_senders:
        return True
    request = parse_with_rules(f"{mail.subject}\n{mail.body}")
    return bool(request.matter_number or request.document_type)


def _quote(folder: str) -> str:
    return f'"{folder}"'


def _fetch(imap: imaplib.IMAP4_SSL, uids: list[bytes]):
    """Yield (uid, raw message) without marking anything read."""
    for uid in uids:
        _, parts = imap.uid("fetch", uid, "(BODY.PEEK[])")
        yield uid, next((p[1] for p in parts if isinstance(p, tuple)), None)


class Mailbox:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch_unread(self) -> list[IncomingEmail]:
        """Return unread messages and mark them read, so each is handled once.

        Requests that the provider filed as spam are moved back to the inbox
        and returned too.
        """
        s = self.settings
        emails = []
        with imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ssl.create_default_context()) as imap:
            imap.login(s.email_address, s.email_password)
            if s.spam_folder:
                try:
                    emails += self._rescue_from_spam(imap)
                except imaplib.IMAP4.error as exc:
                    log.warning("Could not check %s: %s", s.spam_folder, exc)
            imap.select(_quote(s.imap_folder))
            _, data = imap.uid("search", None, "UNSEEN")
            for uid, raw in _fetch(imap, data[0].split()):
                # Claim it before processing: a request that crashes the agent
                # gets an error reply instead of being retried forever.
                imap.uid("store", uid, "+FLAGS", "(\\Seen)")
                if raw is None:
                    continue
                mail = parse_email(raw, uid=uid.decode())
                if mail.sender.lower() == s.email_address.lower():
                    continue
                emails.append(mail)
        return emails

    def _rescue_from_spam(self, imap: imaplib.IMAP4_SSL) -> list[IncomingEmail]:
        s = self.settings
        status, _ = imap.select(_quote(s.spam_folder))
        if status != "OK":
            return []
        _, data = imap.uid("search", None, "UNSEEN")
        rescued = []
        for uid, raw in _fetch(imap, data[0].split()):
            if raw is None:
                continue
            mail = parse_email(raw, uid=uid.decode())
            if not looks_like_request(mail, s):
                continue  # Real spam: left unread and untouched.
            log.info("Moving a request from %s out of %s", mail.sender, s.spam_folder)
            imap.uid("store", uid, "+FLAGS", "(\\Seen)")
            imap.uid("move", uid, _quote(s.imap_folder))
            rescued.append(mail)
        return rescued

    def send_reply(self, original: IncomingEmail, body: str, attachment: Path | None = None) -> None:
        s = self.settings
        reply = build_reply(original, body, s.email_address, s.agent_name, attachment)
        context = ssl.create_default_context()
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context) as smtp:
                smtp.login(s.email_address, s.email_password)
                smtp.send_message(reply)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port) as smtp:
                smtp.starttls(context=context)
                smtp.login(s.email_address, s.email_password)
                smtp.send_message(reply)
        log.info("Replied to %s: %s", original.sender, reply["Subject"])
