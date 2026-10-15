# Mastqual Contract Reader

**Try it:** https://contract.mastqual.in

Paste a contract (a rental agreement, an MSA, an offer letter) and in about a
minute you get a plain-English brief, with the clauses worth a second look
flagged.

One of the **AI tools by [Mastqual Consulting](https://www.mastqual.in)**.
(Formerly called ClearClause.)

> **Not legal advice.** This is a reading aid. It can miss clauses, misread
> text and get things wrong. For a real decision, have a licensed lawyer
> review the full document.

## What you get

- **The verdict:** how balanced the contract looks, in one line.
- **In one sentence:** what you are actually agreeing to.
- **Who must do what**, plus money, dates, lock-in and renewal terms.
- **Red flags and pitfalls:** each one quotes the clause, says why it matters
  and what you could ask to change. It always checks for:
  - IP ownership
  - auto-renewal
  - unequal notice periods
  - who appoints the arbitrator
  - one-sided changes to the terms
  - lopsided liability
  - non-solicit and non-compete clauses
  - exit fees
- **Vague words, missing protections** and **questions to ask** before signing.

## Privacy and limits on the public demo

- Phone numbers, emails, Aadhaar, PAN and similar IDs are **masked before
  anything is sent**. You can also list names and addresses to hide.
- The masked text is sent to an AI provider (Anthropic Claude) to write the
  brief. Contracts are not stored, and the app has no accounts and no history.
- **Up to 3 readings a day per person**, plus an overall daily cap.
- Works best on **6–15 pages of selectable text**. Scanned photos aren't
  supported.

## How it's built

- [Streamlit](https://streamlit.io) app in a single file: `app.py`.
- **AI providers:**
  - Claude Haiku 4.5 is the default.
  - Groq, Google Gemini, xAI Grok and OpenAI also work, through an
    OpenAI-compatible client.
- The AI is asked for **structured JSON**, which the app turns into the
  report. Long contracts are read in chunks and merged.
- **PDF text extraction:** `pypdf`.
- **Hosting:** Streamlit Community Cloud, embedded at contract.mastqual.in.

## Run it yourself

You need Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501. Click **Load a sample rental agreement** to see a
full report without any API key.

To read your own contracts, create `.streamlit/secrets.toml`. It is already
excluded from git, so never commit it.

```toml
API_PROVIDER = "anthropic"   # or "groq", "gemini", "xai", "openai"
API_KEY = "your-key-here"
```

### Optional settings (in the same file)

| Setting | What it does |
|---|---|
| `PUBLIC_MODE = "1"` | Adds the email + "not legal advice" gate and turns on daily limits |
| `DAILY_LIMIT_TOTAL`, `DAILY_LIMIT_PER_USER` | Daily caps (public mode defaults: 100 and 3) |
| `ACCESS_PASSWORD` | Locks the app with a password, for private testing |
| `MODEL` | Overrides the provider's default model |
| `SHOW_SETTINGS = "1"` | Shows the provider settings in the sidebar |
| `GROQ_FREE_TIER = "1"` | Splits text into small chunks to fit Groq's free limits |
| `AI_TIMEOUT_SECONDS`, `MAX_OUTPUT_TOKENS` | Request timeout (default 90 s) and reply size (default 8000) |

## Feedback

Spotted something it missed or got wrong? Use the contact form on
[mastqual.in](https://www.mastqual.in) and pick **"Contract Reader for my team"**,
or message Saroj on LinkedIn.
