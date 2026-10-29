"""Settings, read from environment variables (and a .env file if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-5-5"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(s.strip().lower() for s in os.getenv(name, "").split(",") if s.strip())


@dataclass(frozen=True)
class Settings:
    # Mailbox the agent reads requests from and replies with (IMAP + SMTP).
    email_address: str = ""
    email_password: str = ""
    imap_host: str = ""
    imap_port: int = 993
    imap_folder: str = "INBOX"
    # Requests misfiled as spam are moved back to imap_folder. Empty = don't check.
    spam_folder: str = "[Gmail]/Spam"
    smtp_host: str = ""
    smtp_port: int = 465
    agent_name: str = "UARB Regulatory Agent"
    poll_interval: int = 30
    # Only these senders (addresses or @domains) get a reply. Empty = anyone.
    allowed_senders: tuple[str, ...] = field(default_factory=tuple)

    max_documents: int = 10
    # Most providers cap messages at ~25 MB, and base64 adds a third on top.
    max_attachment_mb: float = 18.0
    anthropic_model: str = DEFAULT_MODEL
    headless: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        # Existing variables win, so .env.local overrides .env.
        load_dotenv(".env.local")
        load_dotenv(".env")
        return cls(
            email_address=os.getenv("AGENT_EMAIL_ADDRESS", ""),
            email_password=os.getenv("AGENT_EMAIL_PASSWORD", ""),
            imap_host=os.getenv("IMAP_HOST", ""),
            imap_port=int(os.getenv("IMAP_PORT", "993")),
            imap_folder=os.getenv("IMAP_FOLDER", "INBOX"),
            spam_folder=os.getenv("SPAM_FOLDER", "[Gmail]/Spam"),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            agent_name=os.getenv("AGENT_NAME", "UARB Regulatory Agent"),
            poll_interval=int(os.getenv("POLL_INTERVAL_SECONDS", "30")),
            allowed_senders=_env_list("ALLOWED_SENDERS"),
            max_documents=int(os.getenv("MAX_DOCUMENTS", "10")),
            max_attachment_mb=float(os.getenv("MAX_ATTACHMENT_MB", "18")),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL),
            headless=_env_bool("HEADLESS", True),
        )

    @property
    def max_attachment_bytes(self) -> int:
        return int(self.max_attachment_mb * 1024 * 1024)

    def missing_email_settings(self) -> list[str]:
        required = {
            "AGENT_EMAIL_ADDRESS": self.email_address,
            "AGENT_EMAIL_PASSWORD": self.email_password,
            "IMAP_HOST": self.imap_host,
            "SMTP_HOST": self.smtp_host,
        }
        return [name for name, value in required.items() if not value]

    def is_allowed_sender(self, address: str) -> bool:
        if not self.allowed_senders:
            return True
        address = address.lower()
        domain = "@" + address.rpartition("@")[2]
        return address in self.allowed_senders or domain in self.allowed_senders
