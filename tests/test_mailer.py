from email import policy
from email.parser import BytesParser

from uarb_agent.config import Settings
from uarb_agent.mailer import IncomingEmail, build_reply, looks_like_request, parse_email

RAW = b"""\
From: Jordan Smith <jordan@example.com>
To: agent@example.com
Subject: Filings request
Message-ID: <abc123@example.com>
Content-Type: text/plain; charset=utf-8

Hi Agent, Can you give me Other Documents files from M12205? Thanks!

On Mon, Oct 20, 2026 at 9:00 AM Agent <agent@example.com> wrote:
> Earlier request about M99999 Exhibits
"""


def test_parse_email_strips_quoted_history():
    mail = parse_email(RAW, uid="7")
    assert mail.sender == "jordan@example.com"
    assert mail.sender_name == "Jordan Smith"
    assert mail.subject == "Filings request"
    assert mail.body == "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"
    assert not mail.automated


def test_html_only_and_auto_replies():
    raw = (
        b"From: Bot <mailer-daemon@example.com>\r\nSubject: Undeliverable\r\n"
        b"Auto-Submitted: auto-replied\r\nContent-Type: text/html\r\n\r\n"
        b"<p>Exhibits for <b>M12205</b></p><br>Thanks &amp; regards"
    )
    mail = parse_email(raw)
    assert mail.automated
    assert "Exhibits for M12205" in mail.body
    assert "Thanks & regards" in mail.body


def test_reply_threads_and_attaches_zip(tmp_path):
    zip_path = tmp_path / "M12205_Other_Documents.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    reply = build_reply(parse_email(RAW), "Hi Jordan,", "agent@example.com", "UARB Agent", zip_path)

    parsed = BytesParser(policy=policy.default).parsebytes(reply.as_bytes())
    assert parsed["Subject"] == "Re: Filings request"
    assert parsed["To"] == "Jordan Smith <jordan@example.com>"
    assert parsed["In-Reply-To"] == "<abc123@example.com>"
    assert parsed["Auto-Submitted"] == "auto-replied"
    attachments = list(parsed.iter_attachments())
    assert [a.get_filename() for a in attachments] == ["M12205_Other_Documents.zip"]
    assert attachments[0].get_content() == zip_path.read_bytes()
    assert parsed.get_body(("plain",)).get_content().strip() == "Hi Jordan,"


def test_spam_rescue_only_takes_requests():
    def mail(sender, body, automated=False):
        return IncomingEmail("1", "", sender, "", "", body, automated=automated)

    anyone = Settings(email_address="agent@example.com")
    assert looks_like_request(mail("a@b.com", "Exhibits for M12205 please"), anyone)
    assert not looks_like_request(mail("a@b.com", "You won a prize!"), anyone)
    assert not looks_like_request(mail("a@b.com", "M12205 exhibits", automated=True), anyone)

    restricted = Settings(email_address="agent@example.com", allowed_senders=("@senpilot.com",))
    assert looks_like_request(mail("ops@senpilot.com", "hello?"), restricted)
    assert not looks_like_request(mail("a@b.com", "Exhibits for M12205"), restricted)


def test_allowed_senders():
    settings = Settings(allowed_senders=("boss@utility.com", "@hydro.example"))
    assert settings.is_allowed_sender("Boss@Utility.com")
    assert settings.is_allowed_sender("analyst@hydro.example")
    assert not settings.is_allowed_sender("someone@else.com")
    assert Settings().is_allowed_sender("anyone@anywhere.com")
