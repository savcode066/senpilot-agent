# Senpilot Agent

**This agent is deployed on Google Cloud. To try it, just email
savioSenpilot@gmail.com**, for example: *"Hi Agent, Can you give me Other
Documents files from M12205? Thanks!"* A reply with a ZIP of the documents
arrives within a few minutes. It only replies to emails sent from an
**@senpilot.com** address.

## How it works

```
Gmail inbox ──► read request ──► UARB website ──► ZIP ──► reply email
   (IMAP)         (Claude)        (Playwright)             (SMTP)
```

1. **Receive.** The agent checks the inbox every 30 seconds over IMAP. It also
   looks in Spam, in case Gmail filtered a request there.
2. **Understand.** Claude reads the email and pulls out the matter number
   (e.g. M12205) and document type (Exhibits, Key Documents, Other Documents,
   Transcripts or Recordings). A regex parser is the fallback.
3. **Fetch.** A headless Chromium browser (Playwright) opens the
   [UARB site](https://uarb.novascotia.ca/fmi/webd/UARB15), searches for the
   matter, reads its details and document counts, opens the requested tab, and
   downloads up to 10 documents with "Go Get It".
4. **Reply.** The documents are zipped and sent back over SMTP with a summary
   of the matter and how many documents it has of each type.

| File | Role |
| --- | --- |
| `uarb_agent/mailer.py` | Reading and sending email |
| `uarb_agent/request_parser.py` | Understanding the request with Claude |
| `uarb_agent/scraper.py` | Driving the UARB website |
| `uarb_agent/packager.py` | Building the ZIP |
| `uarb_agent/reply.py` | Writing the reply |
| `uarb_agent/agent.py` | Running the steps above in order |

It runs in Docker on a small Compute Engine VM.

## Running it yourself

```bash
pip install -e ".[dev]" && playwright install chromium
cp .env.example .env    # add the mailbox and Anthropic API key
python -m uarb_agent run
```
