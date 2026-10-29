# UARB Regulatory Agent

An email agent that collects regulatory filings from the
[Nova Scotia Utility and Review Board](https://uarb.novascotia.ca/fmi/webd/UARB15)
public documents database.

Email it a matter number and a document type. It opens the matter on the UARB
site, downloads up to 10 documents of that type with the site's **GO GET IT**
button, zips them, and replies with the ZIP attached, a summary of the matter,
and a count of every document in it.

```
To:      regulatory.agent@gmail.com
Subject: Filings request

Hi Agent, Can you give me Other Documents files from M12205? Thanks!
```

```
Hi Sam,

M12205 is about the Halifax Regional Water Commission - Windsor Street Exchange
Redevelopment Project - $69,275,000. It relates to Capital Expenditure Approvals
within the Water category. The matter had an initial filing on April 7, 2025 and
a final filing on October 23, 2025. Its status is Closed, with the outcome:
Directions given.

I found 13 Exhibits, 6 Key Documents, 43 Other Documents, and no Transcripts or
Recordings (62 documents in total). I downloaded 10 out of the 43 Other
Documents and am attaching them as a ZIP here.

Best regards,
UARB Regulatory Agent

[attachment: M12205_Other_Documents.zip]
```

That reply is real output from a live run. The counts differ from the example in
the challenge brief (13 / 5 / 21) because the matter has gained documents since
the brief was written.

## How it works

```
IMAP inbox ──► parse request ──► drive UARB site ──► ZIP ──► SMTP reply
  (poll)       Claude, with a     (Playwright,        (size-   (threaded,
               regex fallback      headless Chromium)  capped)  ZIP attached)
```

| Module | Job |
| --- | --- |
| `uarb_agent/mailer.py` | Polls the inbox over IMAP, extracts the request text, sends threaded replies over SMTP |
| `uarb_agent/request_parser.py` | Pulls the matter number and document type out of free-form text with Claude structured output (`claude-opus-5-5`); a regex parser covers the case where Claude is unavailable and fills any gaps |
| `uarb_agent/scraper.py` | Drives the UARB site: search, read metadata and tab counts, open the tab, click GO GET IT on each row, download |
| `uarb_agent/packager.py` | Builds the ZIP (`102674 - Board Order.pdf`, ...) and keeps it under the attachment limit |
| `uarb_agent/reply.py` | Writes the reply from a template, so every number and date comes from the site rather than from a model |
| `uarb_agent/agent.py` | Runs the pipeline for one email and the polling loop |

Claude only interprets the incoming request, which is where language
understanding helps ("the hearing audio for m-12383" becomes Recordings / M12383).
The navigation and the facts in the reply are deterministic. Claude's matter
number is only accepted if its digits actually appear in the email.

### What the UARB site needed

The site is a FileMaker WebDirect app (Vaadin under the hood). It has no API,
element ids are generated per layout, and every click is a server round trip.
These are the quirks the scraper handles:

- **The matter search box ignores early typing.** It only becomes editable after
  the click reaches the server, so the scraper waits for a focused
  `contenteditable` before typing and checks that the text landed.
- **"networkidle" never settles reliably** because of background traffic, so
  every step waits on visible UI state instead.
- **Each tab is a separate layout, and the old list stays on screen until the new
  one arrives.** The scraper marks the current grid as stale, then waits for a
  different grid with rendered rows.
- **The document list is a virtualised grid that recycles its rows.** A row
  picked mid-render can become a different document by the time it is clicked.
  So the scraper waits for the grid to stop changing, and it checks that the
  download dialog names the expected document (on the document tabs files are
  named after their doc number, e.g. `102674.pdf`) before saving anything. Rows
  beyond the rendered window are reached by scrolling.
- **GO GET IT opens a "Download Files" dialog.** It shows one button per file;
  clicking one starts the download.
- **Transcripts and Recordings first ask for a filename** ("Export Field to
  File"). The suggested name contains spaces and commas (`...September 12,
  2022.pdf`), which makes the download button silently do nothing, so the
  scraper replaces it with a URL-safe name.
- **Hearing recordings are ~150 MB MP3s and the server sends no
  Content-Length.** The scraper cancels the browser's copy of each download and
  streams the same session URL itself, stopping as soon as a file outgrows what
  is left of the attachment budget. Recordings are reported as too large in
  about a minute instead of after 40 minutes of downloading.
- **The site occasionally says "No Records Found" for a matter that exists,
  and occasionally ignores a click.** A miss is confirmed with a second search,
  and a failed row is retried once.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env               # then fill it in (.env.local also works and wins)
```

For a Gmail mailbox, turn on 2-step verification for the agent's account,
create an [app password](https://myaccount.google.com/apppasswords), and put it
in `AGENT_EMAIL_PASSWORD`. Any IMAP/SMTP provider works; set the hosts and ports
in `.env`.

`ANTHROPIC_API_KEY` is optional. Without it, requests are parsed with the
rule-based parser, which handles the standard phrasings ("Other Documents",
"key docs", "transcripts", "M12205", "m-12205"). With it, each email costs one
small Claude call: about 770 input and 30 output tokens, roughly $0.004 on
`claude-opus-5-5`. Output is capped at 2,048 tokens per call, and token usage
is logged.

## Usage

```bash
# Run the agent: poll the inbox and answer every new request
python -m uarb_agent run
python -m uarb_agent run --once      # process unread mail once, then exit

# Try the whole pipeline without email: prints the reply, saves the ZIP to ./output
python -m uarb_agent ask "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"

# Just download: matter number + document type
python -m uarb_agent fetch M12205 "Other Documents" --limit 10 --out output

# Watch the browser while it works
python -m uarb_agent --headed fetch M10431 transcripts
```

### Deploying

The agent needs to run continuously, since it polls the inbox. The `Dockerfile`
builds on Microsoft's Playwright image, which includes Chromium. On any Linux
VM with Docker (it runs on a GCP e2-small):

```bash
docker build -t uarb-agent .
docker run -d --name uarb-agent --restart=always \
  -v "$PWD/.env.local:/app/.env.local:ro" uarb-agent
docker logs -f uarb-agent
```

### Configuration

All settings come from environment variables or `.env` (see `.env.example`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_EMAIL_ADDRESS`, `AGENT_EMAIL_PASSWORD` | (required for `run`) | Mailbox the agent reads and sends from |
| `IMAP_HOST`, `IMAP_PORT`, `IMAP_FOLDER` | `993`, `INBOX` | Where requests arrive |
| `SPAM_FOLDER` | `[Gmail]/Spam` | Requests misfiled here are moved back to the inbox and answered; empty to skip |
| `SMTP_HOST`, `SMTP_PORT` | `465` | Outgoing mail (465 = TLS, 587 = STARTTLS) |
| `POLL_INTERVAL_SECONDS` | `30` | How often to check the inbox |
| `ALLOWED_SENDERS` | anyone | Comma-separated addresses or `@domains` the agent will answer |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | none, `claude-opus-5-5` | Claude request parsing (optional) |
| `MAX_DOCUMENTS` | `10` | Documents per request |
| `MAX_ATTACHMENT_MB` | `18` | ZIP size cap (most providers cap messages at 25 MB, and base64 adds a third) |
| `HEADLESS` | `true` | Set to `false` to show the browser |

## Behaviour

| Situation | Reply |
| --- | --- |
| Normal request | Matter summary, counts for all five tabs plus the total, ZIP of up to 10 documents |
| Request filed as spam | Moved back to the inbox and answered. A spam message only counts as a request if it's from an allowed sender and, when `ALLOWED_SENDERS` is empty, mentions a matter number or document type; real spam is left alone |
| Fewer than 10 documents | "I downloaded all 6 Key Documents ..." |
| None of that type | Summary and counts, and says there's nothing to attach |
| Matter doesn't exist | Says so and asks the sender to check the number |
| Matter number or type missing | Asks for what's missing, with an example |
| Files too big to email | Attaches what fits and names what didn't. If the first three files are all over the limit (hearing recordings), it stops and says so |
| A download fails | Retries once, then moves on to the next document and lists the failure |
| UARB site down or changed | Apologises and asks the sender to try later |

Each email is marked read before it is processed, so a request that crashes
the agent gets an error reply instead of being retried forever. Replies carry
`In-Reply-To`/`References` so they thread, and `Auto-Submitted: auto-replied`
so other robots don't answer them. The agent ignores bounces, auto-replies, list
mail, and its own messages.

The "first 10" are the first 10 rows in the site's own order: Other Documents
are newest first, while Exhibits and Key Documents are in filing order.

## Tests

```bash
pytest
```

The unit tests cover request parsing (rules, plus the Claude path with a stub
client), the three row layouts seen on the site, the reply wording (including
the challenge's example), ZIP size limits, and email parsing and threading.
They don't touch the live site.

Live runs of the full agent (Claude parsing plus the real UARB site, without
the email transport), using the brief's wording "Hi Agent, Can you give me
<type> files from <matter>? Thanks!":

| Matter | Exhibits | Key Documents | Other Documents | Transcripts | Recordings |
| --- | --- | --- | --- | --- | --- |
| M12205 | 6 of 13 attached; H-1 (48 MB) and 3 other large filings reported as over the limit | all 6 | 10 of 43 | none in matter, no ZIP | none in matter, no ZIP |
| M12383 | all 6 | all 4 | 10 of 18 | none in matter, no ZIP | none in matter, no ZIP |

Also checked:
- **M10431**, which has all five tabs populated: Transcripts download, and
  Recordings are reported as too large.
- **Informal wording**: "hearing audio for m-10431" maps to Recordings.
- **Incomplete requests**: one with no document type and one with no matter
  number each get a clarification reply.
- **Unknown matter**: M99999 gets a "no such matter" reply.

## Limitations and next steps

- Requests are handled one at a time. Each takes roughly 1–3 minutes.
- Hearing recordings can't go by email. Uploading large files to object
  storage and replying with links would fix that.
- The inbox is polled. IMAP IDLE or a provider webhook (e.g. Gmail push) would
  answer faster.
- For the regulatory research database this feeds, the next step is to keep
  every downloaded file and its metadata (matter, type, doc number, date,
  title) in a store and index the text, rather than only emailing it.
