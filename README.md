# ClearClause

A simple web app that turns contract text into plain English and flags
ambiguous wording and common pitfalls.

This is **not legal advice**. It is a reading aid. For anything important,
talk to a licensed lawyer.

## What version 1 does

- Paste contract text, or upload a PDF
- Produce a common-person summary
- Flag red flags, vague terms, missing protections, and questions to ask
- Work without an API key in **Demo mode** (sample rental agreement)
- Use Groq, Google Gemini, xAI Grok, or OpenAI once you add a key

## What you need on your computer

1. Python 3.10 or newer
2. A terminal (Command Prompt, PowerShell, Terminal, or VS Code)
3. Optional for live analysis: a free API key (Groq is the easiest first key)

## Run it locally (first time)

Open a terminal in this `clearclause` folder, then run:

```bash
python3 -m venv .venv
```

Activate the virtual environment:

- macOS / Linux: `source .venv/bin/activate`
- Windows PowerShell: `.venv\Scripts\Activate.ps1`

Install packages:

```bash
pip install -r requirements.txt
```

Start the app:

```bash
streamlit run app.py
```

Your browser should open `http://localhost:8501`.

Click **Load a sample rental agreement** to see the full report with no API key.

## Get a free API key (so it can read YOUR contracts)

Easiest first option:

1. Open https://console.groq.com/keys
2. Create an account
3. Create an API key
4. In the app sidebar, choose **Groq** and paste the key
   or put it in `.streamlit/secrets.toml` (see the example file)

Other options:

- Google Gemini: https://aistudio.google.com/apikey
- xAI Grok: https://console.x.ai
- OpenAI: https://platform.openai.com/api-keys

Privacy: whatever you paste or upload is sent to the provider you choose.
Do not put highly confidential documents into a third-party API unless you
accept that.

## Put the key in a file instead of the sidebar

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `secrets.toml` and paste the key. Restart Streamlit.

## Next steps (later)

- Host on Streamlit Community Cloud so friends can open a link
- Add Word (.docx) upload
- Add a follow-up question box (“What happens if I leave early?”)
- Split very long contracts into sections

None of that is required to start.
