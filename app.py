"""
Mastqual Contract Reader (formerly ClearClause) — plain-English contract reader for non-lawyers.

Not legal advice. An AI reading aid that can miss things or get them wrong.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
from typing import Any

import streamlit as st
from openai import OpenAI
from pypdf import PdfReader

APP_NAME = "Mastqual Contract Reader"
MAX_CHARS = 60_000
# Public mode reads less, so one visitor cannot burn a large share of the budget.
PUBLIC_MAX_CHARS = 45_000
# Groq's FREE on_demand tier is often 8,000 tokens/minute per model.
# Only used when GROQ_FREE_TIER = "1" in secrets. Paid keys read in one go.
GROQ_CHUNK_CHARS = 14_000

DISCLAIMER = (
    "Mastqual Contract Reader is a reading aid, not a lawyer, not a law firm, and not legal advice. "
    "It can miss clauses, misread text, and get things wrong. "
    "For a real decision, have a licensed lawyer review the full document."
)
BEST_FIT_NOTE = "Works best on 6–15 pages of selectable text (not scanned photos)."

PROVIDERS = {
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "key_help": "https://console.groq.com/keys",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.8-flash",
        "key_help": "https://aistudio.google.com/apikey",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        # Anthropic's OpenAI-compatible endpoint, so the same client code works.
        "base_url": "https://api.anthropic.com/v1/",
        "model": "claude-haiku-4-5",
        "key_help": "https://console.anthropic.com/settings/keys",
    },
    "xai": {
        "label": "xAI Grok",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4-fast",
        "key_help": "https://console.x.ai",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_help": "https://platform.openai.com/api-keys",
    },
}

SYSTEM_PROMPT = """
You are a senior contracts analyst who explains documents to ordinary people.
You are not their lawyer. You never say "you should sign" or "this is fine, sign it".
You never invent clauses that are not in the text.

Write for a busy adult with no legal training. Short sentences. Everyday words.
If you must keep a legal word, define it in brackets the first time.

If the user names a jurisdiction, use that lens. If they choose India, notice
common Indian personal/small-business issues (lock-in, security deposit return,
leave-and-license vs lease, one-sided termination, arbitration far from home,
unlimited indemnity, auto-renewal). Do not quote statutes unless you are sure.

Return ONLY valid JSON with this exact shape:
{
  "document_type_guess": "string",
  "parties": ["string"],
  "one_sentence": "string",
  "verdict": {
    "level": "ok" | "caution" | "high_risk",
    "headline": "string",
    "why": "string"
  },
  "plain_english": ["string", "string", "string"],
  "who_does_what": [{"who": "string", "must": "string"}],
  "money_and_time": {
    "payments": ["string"],
    "dates_and_deadlines": ["string"],
    "term_and_renewal": "string",
    "exit_and_lock_in": "string"
  },
  "red_flags": [
    {
      "severity": "high" | "medium" | "low",
      "title": "string",
      "clause_quote": "short quote from the text, or empty if paraphrased",
      "plain_english": "string",
      "why_it_matters": "string",
      "what_you_can_ask": "string"
    }
  ],
  "ambiguous_terms": [
    {
      "term": "string",
      "quote": "string",
      "why_unclear": "string",
      "how_it_can_hurt": "string"
    }
  ],
  "missing_protections": [{"item": "string", "why_it_matters": "string"}],
  "questions_before_signing": ["string"],
  "limits": "One short note on what this reading might have missed."
}

Always check these, whatever the paper type. Each one that is present MUST get its own entry
in red_flags (not only a mention in the summary or dates):
- Who owns work, ideas, or IP — especially "whether or not related" or outside working hours (high).
- Auto-renewal and the notice window needed to stop it (medium or higher; people miss these deadlines).
- Notice periods that are different for each side (say both numbers in the flag title).
- Who picks the arbitrator or judge. If one side appoints a sole arbitrator alone, flag it high,
  together with any far-away venue.
- One side can change the terms alone (by email, posting, or "continued use" = acceptance): high.
- Lopsided liability: one side's liability is capped while the other's indemnity is unlimited
  (one flag that names both sides).
- Non-solicit / non-hire / non-compete limits after the paper ends (name each one that appears).
- Penalties or fees for leaving early.

Accuracy rules:
- Describe each clause exactly as written. Do not widen it (for example, "leaves" is not "is fired").
- Never mention a clause, right, or obligation that is not in the text. If something is absent,
  put it only under missing_protections and say it is missing.

Rules:
- Prefer fewer, sharper flags over a long laundry list.
- Quote only short snippets.
- Keep the whole JSON compact (under about 1,500 words). Each string is one or two short sentences.
  At most 12 red_flags (checklist items above come first, then anything else), 5 ambiguous_terms,
  5 missing_protections, 10 questions.
- If the text is incomplete, say so in limits and lower your certainty in the verdict why.
- If it does not look like a contract, say that and still extract what you can.
- If a playbook is attached, check every playbook item against the text. Flag hits. List misses under missing_protections.
""".strip()

PLAYBOOKS = {
    "contractor": """
PLAYBOOK — Indian commercial contractor / resource-supply / staffing MSA.
Read as if helping the vendor who supplies people, unless the user said otherwise.
Check these items. Quote a short snippet when the paper has it.
1. Who is the legal employer of the people on site (vendor vs client).
2. Who pays PF, ESI, gratuity, professional tax, GST, and workplace injury claims.
3. Pay-when-paid or pay-if-paid (vendor paid only after the client's customer pays).
4. Payment period, invoice rejection rights, and right to suspend work for non-payment.
5. Client can reject, replace, or keep individuals; notice and bench-cost if any.
6. Non-solicit / non-hire of vendor staff during and after the term; how many months.
7. IP: does the client own everything the resource touches, including vendor tools.
8. Indemnity scope — unlimited, for client negligence, for third-party IP, cap or no cap.
9. SLAs, liquidated damages, 24x7 coverage the vendor cannot control.
10. Termination for convenience vs for cause; notice; payment for work already done.
11. Audit rights and how far they reach into the vendor's other clients.
12. Data / confidentiality that never expires.
13. Governing law and arbitration far from where the vendor actually works.
14. Restriction on the vendor working for competitors or the end-customer directly.
If India: notice whether the paper pretends the people are not employees while controlling hours, place, and tools.
""",
    "rental": """
PLAYBOOK — India residential leave-and-licence / rental.
Check: lock-in and leftover-rent penalty; deposit amount, deductions, and return timeline;
mid-term rent increase; owner entry without notice; one-sided termination; who pays
structural repairs; guests and alterations; arbitration or courts far from the flat;
inventory / condition report missing.
""",
}


def playbook_text(doc_type: str) -> str:
    lowered = (doc_type or "").lower()
    if "contractor" in lowered or "resource" in lowered or "staffing" in lowered:
        return PLAYBOOKS["contractor"]
    if "freelance" in lowered or "consulting" in lowered:
        return PLAYBOOKS["contractor"]
    if "rental" in lowered or "licence" in lowered or "license" in lowered or "house" in lowered:
        return PLAYBOOKS["rental"]
    return ""

SAMPLE_CONTRACT = """
LEAVE AND LICENCE AGREEMENT

This Leave and Licence Agreement is made on 1 October 2026 between
Ramesh Iyer, residing at Koramangala, Bengaluru ("Licensor") and
Priya Shah, residing at Pune ("Licensee").

1. Premises. Licensor grants Licensee a licence to use Flat No. 4B,
   12th Main, HSR Layout, Bengaluru, for residential use only.

2. Term. Eleven (11) months starting 15 October 2026. The licence
   shall automatically renew for further periods of 11 months unless
   either party gives written notice 60 days before expiry. During the
   first 11 months, Licensee may not terminate for any reason. If
   Licensee vacates early, Licensee shall pay rent for the entire
   remaining lock-in period as liquidated damages.

3. Licence Fee. Rs. 45,000 per month, payable on or before the 5th of
   each month. A delay of more than 3 days attracts interest at 3% per
   month and Licensor may disconnect utilities.

4. Security Deposit. Rs. 1,35,000. Refundable within 90 days of
   handover, after deductions for dues, damage, painting, wear and
   tear, and any other amount Licensor considers reasonable.

5. Increases. Licensor may increase the licence fee at any time by
   giving 15 days' notice.

6. Maintenance and repairs. Licensee shall keep the premises in good
   condition and shall bear the cost of all repairs, including structural
   repairs, plumbing, electrical, and appliance replacement, fair wear
   and tear notwithstanding.

7. Access. Licensor and his agents may enter the premises at any time,
   with or without notice, for inspection, repairs, showing to buyers
   or future licensees, or any other purpose.

8. Guests. No person other than Licensee may stay overnight without
   Licensor's prior written consent.

9. Alterations. Licensee shall not put nails in walls or change any
   fixture without consent. Any improvement becomes Licensor's property
   with no compensation.

10. Termination by Licensor. Licensor may terminate this licence by
    giving 7 days' written notice if Licensee breaches any term, or
    immediately if Licensor requires the flat for personal use, sale,
    or any other reason Licensor deems sufficient.

11. Indemnity. Licensee shall indemnify Licensor against all claims,
    losses, taxes, injuries to persons or property, and legal costs,
    whether or not caused by Licensee's fault.

12. Dispute resolution. All disputes shall be referred to sole
    arbitration in Mumbai. Courts in Mumbai have exclusive jurisdiction.
    Licensee shall bear the costs of arbitration.

13. Other terms. This writing is the entire agreement. Licensee has
    read and understood every clause. No oral promise has any effect.
"""

SAMPLE_ANALYSIS = {
    "document_type_guess": "Residential leave-and-licence agreement (Bengaluru flat)",
    "parties": [
        "Ramesh Iyer — Licensor (owner)",
        "Priya Shah — Licensee (the person moving in)",
    ],
    "one_sentence": "You can live in the HSR Layout flat for 11 months, but leaving early, getting your deposit back, and staying if the owner changes his mind are all stacked against you.",
    "verdict": {
        "level": "high_risk",
        "headline": "One-sided rental paper — several clauses can cost you money or force a sudden move.",
        "why": "Lock-in with full remaining rent, a vague deposit, sudden rent increases, owner entry at any time, 7-day (or instant) eviction, and disputes that must be fought in Mumbai.",
    },
    "plain_english": [
        "This is not a sale and not a typical long lease. It is permission to occupy the flat for a fixed period, with rules written by the owner.",
        "You pay ₹45,000 a month plus a ₹1,35,000 deposit. If you miss the 5th of the month by a few days, interest runs at 3% per month and utilities can be cut.",
        "You are locked in for the first 11 months. If you leave early, the paper says you still owe every remaining month.",
        "The owner can raise rent with 15 days' notice, walk in without warning, end the arrangement quickly, and keep part or all of the deposit for almost any 'reasonable' reason.",
        "If there is a fight, you would have to go to arbitration in Mumbai — not Bengaluru — and the paper says you pay those costs.",
    ],
    "who_does_what": [
        {"who": "You (Licensee)", "must": "Pay rent by the 5th, pay a large deposit, do almost all repairs, get written permission for guests or wall nails, and stay for the full lock-in or pay the leftover rent."},
        {"who": "Owner (Licensor)", "must": "Let you use the flat — but he keeps wide rights to enter, raise rent, deduct from the deposit, and end the licence on short notice."},
    ],
    "money_and_time": {
        "payments": [
            "Monthly licence fee: ₹45,000 due by the 5th",
            "Late fee: 3% interest per month after 3 days, plus possible utility cut-off",
            "Security deposit: ₹1,35,000 (3 months)",
            "You also pay repairs, including structural and appliance replacement",
        ],
        "dates_and_deadlines": [
            "Start: 15 October 2026",
            "First term: 11 months",
            "Notice to stop auto-renewal: 60 days before expiry",
            "Deposit return window: up to 90 days after you hand back the keys",
            "Owner can raise rent with 15 days' notice",
        ],
        "term_and_renewal": "11 months, then it renews by itself for another 11 months unless someone gives 60 days' written notice.",
        "exit_and_lock_in": "You cannot leave in the first 11 months without paying rent for every leftover month. The owner can push you out in 7 days for a breach, or immediately if he wants the flat for 'personal use, sale, or any other reason'.",
    },
    "red_flags": [
        {
            "severity": "high",
            "title": "You pay the rest of the lock-in if you leave early",
            "clause_quote": "Licensee shall pay rent for the entire remaining lock-in period as liquidated damages.",
            "plain_english": "Changing job, illness, or a bad flat may still leave you paying months of rent after you have moved out.",
            "why_it_matters": "This is one of the most expensive consumer traps in Indian rental papers.",
            "what_you_can_ask": "Cap early-exit at 1–2 months' rent, or allow exit for job transfer, serious defect, or owner breach.",
        },
        {
            "severity": "high",
            "title": "Deposit can be delayed and cut for almost anything",
            "clause_quote": "after deductions for dues, damage, painting, wear and tear, and any other amount Licensor considers reasonable",
            "plain_english": "The owner decides what is 'reasonable' and can take 90 days to return what is left.",
            "why_it_matters": "Three months' rent can sit with the owner while you try to fund the next house.",
            "what_you_can_ask": "Return within 15–30 days, a written deduction list with bills, and no deduction for normal painting or ordinary wear.",
        },
        {
            "severity": "high",
            "title": "Owner can end it in 7 days — or immediately",
            "clause_quote": "or immediately if Licensor requires the flat for personal use, sale, or any other reason Licensor deems sufficient",
            "plain_english": "Your stay is not stable. 'Any other reason' is a blank cheque.",
            "why_it_matters": "You could be asked to leave while you are still locked in from your side.",
            "what_you_can_ask": "Same notice period both ways (30–60 days) and delete 'any other reason'.",
        },
        {
            "severity": "high",
            "title": "Rent can go up at any time",
            "clause_quote": "Licensor may increase the licence fee at any time by giving 15 days' notice",
            "plain_english": "₹45,000 is not a fixed price for the 11 months.",
            "why_it_matters": "A mid-term jump can make the lock-in even more painful.",
            "what_you_can_ask": "Freeze rent for the first term. After that, cap any rise (for example 5–8% on renewal only).",
        },
        {
            "severity": "medium",
            "title": "You pay for structural repairs",
            "clause_quote": "including structural repairs, plumbing, electrical, and appliance replacement, fair wear and tear notwithstanding",
            "plain_english": "A leaking roof or dead geyser could land on you, even when it is not your fault. The usual 'wear and tear' protection is switched off.",
            "why_it_matters": "Repair costs can exceed months of rent.",
            "what_you_can_ask": "Owner pays structure, seepage, and appliances that came with the flat. You pay only damage you cause.",
        },
        {
            "severity": "medium",
            "title": "Disputes are in Mumbai, and you pay",
            "clause_quote": "All disputes shall be referred to sole arbitration in Mumbai... Licensee shall bear the costs",
            "plain_english": "The flat is in Bengaluru. Fighting a case in Mumbai is slow and expensive, and the paper dumps the bill on you.",
            "why_it_matters": "Unfair venue clauses often stop people from enforcing their rights.",
            "what_you_can_ask": "Bengaluru courts or Bengaluru arbitration, and split costs unless you clearly lose.",
        },
        {
            "severity": "medium",
            "title": "Owner can walk in at any hour",
            "clause_quote": "may enter the premises at any time, with or without notice",
            "plain_english": "This undercuts basic privacy in your home.",
            "why_it_matters": "Entry-without-notice clauses are easy to misuse.",
            "what_you_can_ask": "24–48 hours' written notice except true emergencies.",
        },
        {
            "severity": "low",
            "title": "Unlimited indemnity",
            "clause_quote": "whether or not caused by Licensee's fault",
            "plain_english": "You may have to cover the owner's losses even when you did nothing wrong.",
            "why_it_matters": "Open-ended indemnity is a classic one-sided term.",
            "what_you_can_ask": "Limit indemnity to harm you actually cause through neglect or misuse.",
        },
    ],
    "ambiguous_terms": [
        {
            "term": "any other amount Licensor considers reasonable",
            "quote": "any other amount Licensor considers reasonable",
            "why_unclear": "There is no list, cap, or need for bills.",
            "how_it_can_hurt": "The deposit can shrink for painting, 'wear and tear', or unnamed extras.",
        },
        {
            "term": "any other reason Licensor deems sufficient",
            "quote": "personal use, sale, or any other reason Licensor deems sufficient",
            "why_unclear": "'Sufficient' is whatever the owner says it is.",
            "how_it_can_hurt": "You can be asked to leave on short notice for reasons you cannot predict.",
        },
        {
            "term": "good condition / fair wear and tear notwithstanding",
            "quote": "fair wear and tear notwithstanding",
            "why_unclear": "Ordinary ageing of a home is usually the owner's cost. This phrase tries to flip that.",
            "how_it_can_hurt": "You may be billed for faded paint, ageing pipes, or a worn stove.",
        },
        {
            "term": "liquidated damages",
            "quote": "pay rent for the entire remaining lock-in period as liquidated damages",
            "why_unclear": "It is labelled as a pre-agreed loss, but it may be far higher than the owner's actual loss if the flat is re-let.",
            "how_it_can_hurt": "You could pay rent on a flat you no longer use.",
        },
    ],
    "missing_protections": [
        {"item": "No inventory or condition report", "why_it_matters": "Without dated photos and a signed list of fixtures, deposit fights become 'your word vs theirs'."},
        {"item": "No cap on deposit deductions or a timeline with interest for delay", "why_it_matters": "90 days with open-ended cuts is a long time to wait for a large sum."},
        {"item": "No owner duty to provide basic habitability", "why_it_matters": "Water, power, seepage, and major fittings are not clearly the owner's job."},
        {"item": "No privacy / notice-before-entry rule", "why_it_matters": "A home needs a minimum notice period except emergencies."},
        {"item": "No equal exit rights", "why_it_matters": "You are locked in; the owner is not."},
    ],
    "questions_before_signing": [
        "Will you delete the full remaining-rent penalty and allow a 30- or 60-day exit after an initial 1–2 month minimum stay?",
        "Will rent stay frozen for these 11 months, with any increase only at renewal and capped?",
        "Will the deposit come back in 15–30 days with a written, billed deduction list, and no charge for ordinary painting?",
        "Will entry require 24–48 hours' notice except emergencies?",
        "Will major and structural repairs stay with the owner?",
        "Will disputes be in Bengaluru, not Mumbai, with shared arbitration costs?",
        "Can we attach photos, a fixture list, and the Society / maintenance rules as part of the agreement?",
    ],
    "limits": "This is a short sample drafted to teach the app. A real paper may have annexures, society rules, or side WhatsApp promises that change the picture. This is not legal advice.",
}


def load_secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    if value:
        return str(value).strip()
    env_names = {
        "API_KEY": ["API_KEY", "GROQ_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"],
        "API_PROVIDER": ["API_PROVIDER"],
        "MODEL": ["MODEL"],
    }
    for env_name in env_names.get(name, [name]):
        env_val = (os.environ.get(env_name) or "").strip()
        if env_val:
            return env_val
    return ""


def secret_flag(name: str) -> bool:
    return load_secret(name).lower() in {"1", "true", "yes", "on"}


def secret_int(name: str, default: int) -> int:
    try:
        return int(load_secret(name) or default)
    except ValueError:
        return default


def is_public_mode() -> bool:
    return secret_flag("PUBLIC_MODE")


def get_settings() -> dict[str, str]:
    default_provider = load_secret("API_PROVIDER") or "groq"
    if default_provider not in PROVIDERS:
        default_provider = "groq"

    saved_key = load_secret("API_KEY")
    # When the owner has saved a key in Secrets, visitors never see the AI setup.
    # Set SHOW_SETTINGS = "1" only for your own comparison sessions.
    if saved_key and not secret_flag("SHOW_SETTINGS"):
        return {
            "provider": default_provider,
            "api_key": saved_key,
            "model": load_secret("MODEL") or PROVIDERS[default_provider]["model"],
        }

    st.sidebar.header("AI setup")
    st.sidebar.caption(
        "No key? Use **Demo mode** on the main page. "
        "Groq is the fastest free key for a first test."
    )
    provider = st.sidebar.selectbox(
        "Provider",
        options=list(PROVIDERS.keys()),
        index=list(PROVIDERS.keys()).index(default_provider),
        format_func=lambda key: PROVIDERS[key]["label"],
    )
    if saved_key:
        st.sidebar.success("API key loaded from secrets.toml (or environment).")
    key = st.sidebar.text_input(
        "API key (leave blank if already saved)",
        value="" if saved_key else "",
        type="password",
        help="Preferred: save the key in .streamlit/secrets.toml. The sidebar is only a fallback.",
    )
    key = key.strip() or saved_key
    model = st.sidebar.text_input(
        "Model name",
        value=load_secret("MODEL") or PROVIDERS[provider]["model"],
    )
    st.sidebar.markdown(f"Get a key: {PROVIDERS[provider]['key_help']}")
    if provider == "groq" and secret_flag("GROQ_FREE_TIER"):
        st.sidebar.caption(
            "Groq free plan: about 8,000 tokens per minute. "
            "Long PDFs are split into parts automatically. "
            "If a call still fails, wait 60 seconds or switch to Gemini."
        )
    st.sidebar.divider()
    st.sidebar.markdown(
        "**Public site later:** put `API_PROVIDER` and `API_KEY` in "
        "Streamlit Community Cloud → App settings → Secrets. "
        "Do not commit secrets.toml to GitHub."
    )
    return {"provider": provider, "api_key": key.strip(), "model": model.strip()}


def parse_hide_words(raw: str) -> list[str]:
    parts = re.split(r"[\n,;]+", raw or "")
    words = [part.strip() for part in parts if len(part.strip()) >= 2]
    words.sort(key=len, reverse=True)
    return words


def mask_identifiers(text: str, extra_terms: list[str] | None = None) -> tuple[str, dict[str, int]]:
    """Replace common IDs on the Mastqual Contract Reader server. Nothing goes to the AI provider during this step."""
    counts: dict[str, int] = {}
    out = text

    def apply(label: str, pattern: str, flags: int = 0) -> None:
        nonlocal out
        out, n = re.subn(pattern, f"[{label}]", out, flags=flags)
        if n:
            counts[label] = counts.get(label, 0) + n

    apply("EMAIL", r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
    # Aadhaar first, so its 4-4-4 groups are not half-eaten by the phone rules.
    apply("AADHAAR", r"(?<!\d)\d{4}[\s-]\d{4}[\s-]\d{4}(?!\d)")
    # Indian mobiles: 9845147258, 98451 47258, 98451-47258, 984 514 7258, with or without +91 / 0.
    apply("PHONE", r"(?<![\d+])(?:\+?91[\s-]*|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
    apply("PHONE", r"(?<![\d+])(?:\+?91[\s-]*|0)?[6-9]\d{2}[\s-]\d{3}[\s-]\d{4}(?!\d)")
    # Landlines like 080 2345 6789 or 080-23456789.
    apply("PHONE", r"(?<!\d)0\d{2,4}[\s-]\d{3,4}[\s-]?\d{3,4}(?!\d)")
    # Other international numbers, e.g. +1 415 555 0134, +44 20 7946 0958.
    apply("PHONE", r"\+\d{1,3}[\s-]?\(?\d{1,4}\)?(?:[\s-]?\d{2,4}){2,4}(?!\d)")
    apply("PAN", r"\b[A-Z]{5}\d{4}[A-Z]\b", re.I)
    apply("GSTIN", r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
    apply("IFSC", r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
    apply("CIN", r"\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")
    apply("ACCOUNT", r"\b\d{11,18}\b")

    for term in extra_terms or []:
        pattern = rf"(?i)(?<!\w){re.escape(term)}(?!\w)"
        out, n = re.subn(pattern, "[HIDDEN]", out)
        if n:
            counts["HIDDEN"] = counts.get("HIDDEN", 0) + n
    return out, counts


def format_mask_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "No emails, phones, PAN, GSTIN, Aadhaar, IFSC, or account-like numbers found."
    order = ["EMAIL", "PHONE", "AADHAAR", "PAN", "GSTIN", "IFSC", "CIN", "ACCOUNT", "HIDDEN"]
    bits = [f"{counts[key]} {key.lower()}" for key in order if key in counts]
    extra = [f"{label} × {n}" for label, n in counts.items() if label not in order]
    return "Masked before sending: " + ", ".join(bits + extra) + "."


def extract_pdf_text(uploaded) -> str:
    reader = PdfReader(uploaded)
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(text[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("The model did not return usable JSON. Try again or switch model.")


def chunk_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    blocks = re.split(r"\n\s*\n", text)
    buf = ""
    for block in blocks:
        piece = block.strip()
        if not piece:
            continue
        if len(buf) + len(piece) + 2 <= max_chars:
            buf = f"{buf}\n\n{piece}" if buf else piece
            continue
        if buf:
            parts.append(buf)
            buf = ""
        if len(piece) <= max_chars:
            buf = piece
            continue
        for i in range(0, len(piece), max_chars):
            parts.append(piece[i : i + max_chars])
    if buf:
        parts.append(buf)
    return parts or [text[:max_chars]]


def merge_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if len(reports) == 1:
        return reports[0]
    rank = {"high_risk": 3, "caution": 2, "ok": 1}
    worst = max(reports, key=lambda r: rank.get(((r.get("verdict") or {}).get("level") or ""), 0))
    verdict = dict(worst.get("verdict") or {})
    why_bits = [str((r.get("verdict") or {}).get("why") or "").strip() for r in reports]
    why_bits = [b for b in why_bits if b]
    if why_bits:
        verdict["why"] = " ".join(dict.fromkeys(why_bits))

    def collect(key: str) -> list:
        out: list = []
        for report in reports:
            val = report.get(key) or []
            if isinstance(val, list):
                out.extend(val)
        return out

    money_bits = [r.get("money_and_time") or {} for r in reports]
    payments: list[str] = []
    dates: list[str] = []
    for block in money_bits:
        payments.extend(block.get("payments") or [])
        dates.extend(block.get("dates_and_deadlines") or [])
    term = next((b.get("term_and_renewal") for b in money_bits if b.get("term_and_renewal")), "")
    exit_ = next((b.get("exit_and_lock_in") for b in money_bits if b.get("exit_and_lock_in")), "")

    parties: list[str] = []
    for report in reports:
        for person in report.get("parties") or []:
            if person not in parties:
                parties.append(person)

    limits = " Read in parts because the paper was long."
    extra = " ".join(str(r.get("limits") or "") for r in reports).strip()
    if extra:
        limits = extra + limits

    return {
        "document_type_guess": next((r.get("document_type_guess") for r in reports if r.get("document_type_guess")), ""),
        "parties": parties,
        "one_sentence": next((r.get("one_sentence") for r in reports if r.get("one_sentence")), ""),
        "verdict": verdict,
        "plain_english": collect("plain_english")[:8],
        "who_does_what": collect("who_does_what"),
        "money_and_time": {
            "payments": payments[:8],
            "dates_and_deadlines": dates[:8],
            "term_and_renewal": term,
            "exit_and_lock_in": exit_,
        },
        "red_flags": collect("red_flags")[:12],
        "ambiguous_terms": collect("ambiguous_terms")[:10],
        "missing_protections": collect("missing_protections")[:8],
        "questions_before_signing": collect("questions_before_signing")[:12],
        "limits": limits,
    }


def explain_ai_error(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "413" in text or "429" in text or "tokens per minute" in low or "rate_limit" in low or "rate limit" in low:
        return (
            "Mastqual Contract Reader is busy right now (the AI provider's rate limit was reached). "
            "Wait a minute and click Simplify again, or paste fewer pages. "
            + BEST_FIT_NOTE
        )
    if (
        "503" in text or "529" in text or "overloaded" in low or "high demand" in low
        or "unavailable" in low or "timed out" in low or "timeout" in low
    ):
        return (
            "The AI provider is busy right now and did not answer in time. "
            "Please wait a minute and click Simplify again."
        )
    if "401" in text or "invalid_api_key" in low or "incorrect api key" in low:
        if is_public_mode():
            return "Mastqual Contract Reader is temporarily unavailable. Please try again later."
        return "The API key was rejected. Copy a fresh key from the provider dashboard and paste it again."
    if "model" in low and (
        "not found" in low or "not_found" in low or "does not exist" in low or "no longer available" in low
    ):
        if is_public_mode():
            return "Mastqual Contract Reader is temporarily unavailable. Please try again later."
        return "That model name is not available on this key. Leave the default, or pick another model the provider lists."
    if "expecting" in low or "delimiter" in low or "json" in low:
        return (
            "The model returned a broken briefing (the reply was cut off or not valid JSON). "
            "Wait 60 seconds and click Simplify again. "
            "If it keeps failing, paste fewer pages."
        )
    if is_public_mode():
        # Do not show raw provider errors (they can include account details) to strangers.
        return "The AI call failed. Please try again in a minute."
    return f"The AI call failed. Technical detail: {exc}"


def call_model(client: OpenAI, settings: dict[str, str], user_prompt: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=settings["model"],
                temperature=0.1 if attempt else 0.2,
                max_tokens=secret_int("MAX_OUTPUT_TOKENS", 8000),
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": user_prompt
                        if attempt == 0
                        else user_prompt + "\n\nReturn compact valid JSON only. No markdown.",
                    },
                ],
            )
            content = response.choices[0].message.content or ""
            return extract_json(content)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            time.sleep(2)
    raise last_error or ValueError("The model did not return usable JSON.")


def analyze_contract(
    contract_text: str,
    doc_type: str,
    jurisdiction: str,
    reader_role: str,
    settings: dict[str, str],
) -> dict[str, Any]:
    client = OpenAI(
        api_key=settings["api_key"],
        base_url=PROVIDERS[settings["provider"]]["base_url"],
        # Stop waiting on a stuck provider, so visitors get a clear message instead of a spinner.
        timeout=float(secret_int("AI_TIMEOUT_SECONDS", 90)),
        max_retries=1,
    )
    groq_free = settings["provider"] == "groq" and secret_flag("GROQ_FREE_TIER")
    chunk_size = GROQ_CHUNK_CHARS if groq_free else secret_int("CHUNK_CHARS", MAX_CHARS)
    chunks = chunk_text(contract_text, chunk_size)
    reports: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        if index > 1 and groq_free:
            st.info(f"Waiting 20 seconds so Groq’s per-minute cap can reset. Part {index} of {len(chunks)}…")
            time.sleep(20)
        if len(chunks) > 1:
            st.caption(f"Reading part {index} of {len(chunks)}…")
        part_note = ""
        if len(chunks) > 1:
            part_note = (
                f"This is part {index} of {len(chunks)} of the same contract. "
                "Flag only what appears in this part. Do not invent the rest. "
                "Keep red_flags to 4 or fewer. Keep quotes under 25 words.\n\n"
            )
        redaction_note = ""
        if re.search(r"\[(EMAIL|PHONE|PAN|GSTIN|AADHAAR|IFSC|CIN|ACCOUNT|HIDDEN)\]", chunk):
            redaction_note = (
                "Some identifiers were replaced with placeholders such as [EMAIL] or [PAN]. "
                "Treat those as redactions. Analyse the commercial terms. "
                "Do not try to guess the hidden identities.\n"
            )
        book = playbook_text(doc_type)
        user_prompt = (
            f"Document type hint: {doc_type}\n"
            f"Jurisdiction hint: {jurisdiction}\n"
            f"Read this as if you are helping: {reader_role}\n"
            f"{redaction_note}"
            f"{part_note}"
            f"{book}\n"
            f"CONTRACT TEXT:\n{chunk}"
        )
        reports.append(call_model(client, settings, user_prompt))
    return merge_reports(reports)


def verdict_style(level: str) -> tuple[str, str]:
    mapping = {
        "ok": ("Reasonably balanced — still read the flags", "#0f7b4a"),
        "caution": ("Needs care — several terms can bite", "#b45309"),
        "high_risk": ("High risk — several one-sided or vague terms", "#b42318"),
    }
    return mapping.get(level, ("Needs a careful read", "#334155"))


def chip(label: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;padding:2px 9px;border-radius:999px;'
        f"background:{color}20;color:{color};font-size:0.78rem;"
        f'font-weight:600;letter-spacing:0.02em;">{label}</span>'
    )


def render_report(data: dict[str, Any], source_label: str) -> None:
    verdict = data.get("verdict") or {}
    level = str(verdict.get("level") or "caution")
    title, color = verdict_style(level)

    st.markdown(
        f"""
        <div style="border:1px solid {color}33;background:{color}0d;
        border-radius:16px;padding:18px 20px;margin:8px 0 18px;">
          <div style="font-size:0.8rem;color:{color};font-weight:700;
          text-transform:uppercase;letter-spacing:0.06em;">{title}</div>
          <div style="font-size:1.35rem;font-weight:700;margin:6px 0 8px;
          color:#0f172a;line-height:1.3;">{verdict.get("headline", "")}</div>
          <div style="color:#334155;line-height:1.55;">{verdict.get("why", "")}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.caption(source_label)
    guess = data.get("document_type_guess")
    if guess:
        st.markdown(f"**Looks like:** {guess}")
    one = data.get("one_sentence")
    if one:
        st.markdown(f"**In one sentence:** {one}")

    parties = data.get("parties") or []
    if parties:
        st.markdown("**Parties named**")
        for person in parties:
            st.markdown(f"- {person}")

    st.subheader("Plain English")
    for para in data.get("plain_english") or []:
        st.markdown(f"- {para}")

    st.subheader("Who must do what")
    for row in data.get("who_does_what") or []:
        st.markdown(f"**{row.get('who', 'Party')}** — {row.get('must', '')}")

    money = data.get("money_and_time") or {}
    st.subheader("Money, dates, and lock-in")
    cols = st.columns(2)
    with cols[0]:
        st.markdown("**Payments**")
        for item in money.get("payments") or []:
            st.markdown(f"- {item}")
    with cols[1]:
        st.markdown("**Dates and deadlines**")
        for item in money.get("dates_and_deadlines") or []:
            st.markdown(f"- {item}")
    if money.get("term_and_renewal"):
        st.markdown(f"**Term / renewal:** {money['term_and_renewal']}")
    if money.get("exit_and_lock_in"):
        st.markdown(f"**Leaving early:** {money['exit_and_lock_in']}")

    st.subheader("Red flags and pitfalls")
    flags = data.get("red_flags") or []
    if not flags:
        st.write("No sharp pitfalls were flagged. That is not a green light — it may only mean the text was short or unclear.")
    for flag in flags:
        severity = str(flag.get("severity") or "medium").lower()
        color = {"high": "#b42318", "medium": "#b45309", "low": "#2563eb"}.get(severity, "#475569")
        st.markdown(
            f"{chip(severity.upper(), color)}  **{flag.get('title', 'Flag')}**",
            unsafe_allow_html=True,
        )
        if flag.get("clause_quote"):
            st.markdown(f"> {flag['clause_quote']}")
        st.markdown(flag.get("plain_english", ""))
        if flag.get("why_it_matters"):
            st.markdown(f"*Why it matters:* {flag['why_it_matters']}")
        if flag.get("what_you_can_ask"):
            st.markdown(f"*What you can ask to change:* {flag['what_you_can_ask']}")
        st.markdown("")

    st.subheader("Ambiguous wording")
    terms = data.get("ambiguous_terms") or []
    if not terms:
        st.write("No especially vague phrases were singled out.")
    for term in terms:
        st.markdown(f"**{term.get('term', 'Term')}**")
        if term.get("quote"):
            st.markdown(f"> {term['quote']}")
        st.markdown(f"{term.get('why_unclear', '')} {term.get('how_it_can_hurt', '')}".strip())

    missing = data.get("missing_protections") or []
    if missing:
        st.subheader("What appears to be missing")
        for row in missing:
            st.markdown(f"- **{row.get('item', '')}** — {row.get('why_it_matters', '')}")

    questions = data.get("questions_before_signing") or []
    if questions:
        st.subheader("Questions to ask before you sign")
        for i, q in enumerate(questions, start=1):
            st.markdown(f"{i}. {q}")

    limits = data.get("limits")
    if limits:
        st.info(limits)

    st.warning(DISCLAIMER)


# ---------------------------------------------------------------------------
# Usage limits (protect the API key and budget)
# ---------------------------------------------------------------------------
# Counts live in server memory. They reset at midnight (India time) and if the
# app restarts, so ALSO set a monthly spend limit in the AI provider's console.

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


@st.cache_resource
def usage_store() -> dict[str, Any]:
    return {"lock": threading.Lock(), "day": "", "total": 0, "by_user": {}}


def visitor_ids() -> list[str]:
    """Hashed IDs used only for daily limits: the email given at the gate, and the IP if Streamlit exposes it."""
    ids: list[str] = []
    email = st.session_state.get("gate_email", "")
    if email:
        ids.append("e:" + hashlib.sha256(email.lower().encode()).hexdigest()[:16])
    ip = ""
    try:
        ip = getattr(st.context, "ip_address", None) or ""
        if not ip:
            fwd = (st.context.headers or {}).get("X-Forwarded-For", "")
            ip = fwd.split(",")[0].strip()
    except Exception:
        ip = ""
    if ip:
        ids.append("i:" + hashlib.sha256(ip.encode()).hexdigest()[:16])
    if "session_id" not in st.session_state:
        st.session_state.session_id = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
    ids.append("s:" + st.session_state.session_id)
    return ids


def take_usage_slot() -> str:
    """Return "" if this visitor may run one more analysis now, else a friendly reason."""
    total_cap = secret_int("DAILY_LIMIT_TOTAL", 100 if is_public_mode() else 0)
    per_user_cap = secret_int("DAILY_LIMIT_PER_USER", 3 if is_public_mode() else 0)
    if not total_cap and not per_user_cap:
        return ""
    store = usage_store()
    today = dt.datetime.now(IST).strftime("%Y-%m-%d")
    ids = visitor_ids()
    with store["lock"]:
        if store["day"] != today:
            store.update(day=today, total=0, by_user={})
        if total_cap and store["total"] >= total_cap:
            return (
                "Today's free readings are used up. Please come back tomorrow, "
                "or write to saroj@mastqual.in for a walkthrough."
            )
        if per_user_cap and any(store["by_user"].get(i, 0) >= per_user_cap for i in ids):
            return (
                f"You have used today's {per_user_cap} free readings. Please come back tomorrow."
            )
        store["total"] += 1
        for i in ids:
            store["by_user"][i] = store["by_user"].get(i, 0) + 1
    return ""


# ---------------------------------------------------------------------------
# Access gates
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I)


def public_gate() -> bool:
    """Light gate for public visitors: an email and a not-legal-advice acknowledgement."""
    if st.session_state.get("gate_email"):
        return True
    st.title(APP_NAME)
    st.markdown("A first read of a contract, in plain English. An AI tool built by Mastqual.")
    st.warning(DISCLAIMER)
    st.caption(
        "Your email is used only to apply a small daily limit so the free demo stays up. "
        "It is not sent to the AI and is not kept after the app restarts. "
        + BEST_FIT_NOTE
    )
    with st.form("gate"):
        email = st.text_input("Your email")
        agree = st.checkbox("I understand this tool is not a lawyer and this is not legal advice.")
        go = st.form_submit_button("Start", type="primary")
    if go:
        if not EMAIL_RE.match(email.strip()):
            st.error("Please enter a valid email address.")
        elif not agree:
            st.error("Please tick the box to continue.")
        else:
            st.session_state.gate_email = email.strip()
            st.rerun()
    return False


def require_access() -> bool:
    """Public mode: email gate. Otherwise: lock with ACCESS_PASSWORD when set (private testers)."""
    if is_public_mode():
        return public_gate()
    expected = load_secret("ACCESS_PASSWORD")
    if not expected:
        return True
    if st.session_state.get("unlocked"):
        return True
    st.title(APP_NAME)
    st.caption("Private preview. Ask the person who sent you the link for the share password.")
    password = st.text_input("Share password", type="password")
    if st.button("Enter", type="primary"):
        if password == expected:
            st.session_state.unlocked = True
            st.rerun()
        st.error("That password does not match.")
    st.caption("Not legal advice. Do not upload highly confidential papers.")
    return False


def inject_css() -> None:
    st.markdown(
        """
        <style>
          .block-container { max-width: 980px; padding-top: 1.4rem; }
          h1 { letter-spacing: -0.03em; }
          textarea { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="📄", layout="centered")
    inject_css()
    if not require_access():
        return
    settings = get_settings()

    st.title(APP_NAME)
    st.markdown(
        "Paste a contract or upload a PDF. Get a **plain-English read**, "
        "plus the **vague phrases** and **pitfalls** a non-lawyer should notice."
    )
    st.warning("**Not a lawyer. Not legal advice.** " + DISCLAIMER.split(". ", 1)[1])
    st.caption(
        "Privacy: the text in the preview below is sent to an AI provider "
        f"({PROVIDERS[settings['provider']]['label']}) to write your briefing. "
        "Mastqual Contract Reader does not store your contract — there are no accounts and no history. "
        + BEST_FIT_NOTE
    )

    if "contract_text" not in st.session_state:
        st.session_state.contract_text = ""
    if "report" not in st.session_state:
        st.session_state.report = None
    if "report_source" not in st.session_state:
        st.session_state.report_source = ""
    if "hide_words" not in st.session_state:
        st.session_state.hide_words = ""

    left, right = st.columns([1, 1])
    with left:
        doc_type = st.selectbox(
            "What kind of paper is this?",
            [
                "Not sure — guess",
                "Commercial contractor / resource-supply (staffing MSA)",
                "House / flat rental or leave-and-licence",
                "Employment or offer letter",
                "Freelance / consulting agreement",
                "NDA / confidentiality",
                "Website or app terms",
                "Loan or credit",
                "Sale of goods or services",
                "Other",
            ],
        )
    with right:
        jurisdiction = st.selectbox(
            "Where does this mainly apply?",
            [
                "India",
                "United States",
                "United Kingdom",
                "European Union",
                "Other / mixed / not sure",
            ],
        )
    reader_role = st.selectbox(
        "Read it as if you are helping…",
        [
            "The person being asked to sign (consumer / employee / tenant / vendor)",
            "A small business owner being asked to sign",
            "Either side — stay neutral",
        ],
    )

    tab_paste, tab_pdf = st.tabs(["Paste text", "Upload PDF"])
    with tab_paste:
        pasted = st.text_area(
            "Contract text",
            value=st.session_state.contract_text,
            height=260,
            placeholder="Paste the contract here…",
        )
        if pasted != st.session_state.contract_text:
            st.session_state.contract_text = pasted
    with tab_pdf:
        uploaded = st.file_uploader("PDF with selectable text (6–15 pages works best)", type=["pdf"])
        if uploaded is not None:
            try:
                extracted = extract_pdf_text(uploaded)
            except Exception as exc:
                st.error(f"Could not read that PDF: {exc}")
            else:
                if len(extracted) < 80:
                    st.warning(
                        "Almost no text came out. This is often a scanned / image PDF. "
                        "Version 1 cannot do OCR yet — paste the text, or use a PDF that already has selectable text."
                    )
                else:
                    st.session_state.contract_text = extracted
                    st.success(f"Extracted {len(extracted):,} characters from {uploaded.name}.")
                    with st.expander("Preview extracted text"):
                        st.text(extracted[:4000] + ("…" if len(extracted) > 4000 else ""))

    st.subheader("Before anything is sent")
    st.caption(
        "Masking runs on the Mastqual Contract Reader server before anything goes to the AI provider. "
        "The preview below is the exact text that will be sent if you click Simplify. "
        "Masking is imperfect — names, addresses, and unique facts can still identify someone, "
        "so add them to the box below."
    )
    mask_on = st.checkbox(
        "Mask IDs and contacts before sending",
        value=True,
        help="Replaces emails, phones, PAN, GSTIN, Aadhaar, IFSC, company CIN, and long account numbers.",
    )
    hide_words = st.text_area(
        "Also hide these words (names, addresses, client names)",
        value=st.session_state.hide_words,
        height=70,
        placeholder="Priya Shah, HSR Layout, Acme Staffing, 12th Main",
    )
    st.session_state.hide_words = hide_words

    raw_text = (st.session_state.contract_text or "").strip()
    extra_terms = parse_hide_words(hide_words) if mask_on else []
    if mask_on and raw_text:
        outbound_text, mask_counts = mask_identifiers(raw_text, extra_terms)
    else:
        outbound_text, mask_counts = raw_text, {}

    if raw_text:
        if mask_on:
            st.info(format_mask_counts(mask_counts))
        else:
            st.warning("Masking is off. The original text below will be sent to the provider.")
        with st.expander("Preview what will be sent", expanded=True):
            preview = outbound_text if len(outbound_text) <= 6000 else outbound_text[:6000] + "\n\n… [truncated in preview only]"
            st.text(preview)
            st.caption(f"{len(outbound_text):,} characters ready to send.")

    c1, c2, c3 = st.columns([1.2, 1.2, 1])
    with c1:
        demo = st.button("Load a sample rental agreement", use_container_width=True)
    with c2:
        run = st.button("Simplify this contract", type="primary", use_container_width=True)
    with c3:
        clear = st.button("Clear", use_container_width=True)

    if clear:
        st.session_state.contract_text = ""
        st.session_state.hide_words = ""
        st.session_state.report = None
        st.session_state.report_source = ""
        st.rerun()

    if demo:
        st.session_state.contract_text = SAMPLE_CONTRACT.strip()
        st.session_state.report = SAMPLE_ANALYSIS
        st.session_state.report_source = "Demo report — sample Bengaluru leave-and-licence. No API key used."
        st.rerun()

    max_chars = PUBLIC_MAX_CHARS if is_public_mode() else MAX_CHARS
    if run:
        text = (outbound_text or "").strip()
        limit_reason = ""
        if not raw_text:
            st.error("Paste text or upload a PDF first. Or click the sample button to see how a report looks.")
        elif len(text) > max_chars:
            st.error(
                f"This text is {len(text):,} characters. Mastqual Contract Reader reads up to "
                f"{max_chars:,} characters (about 15 pages). Paste the key sections for now. "
                + BEST_FIT_NOTE
            )
        elif not settings["api_key"]:
            st.error(
                "Live analysis needs an API key. Use **Load a sample rental agreement** "
                "right now, or add a key in the sidebar to analyse your own paper."
            )
        elif limit_reason := take_usage_slot():
            st.warning(limit_reason)
        else:
            with st.spinner("Reading the fine print… this usually takes under a minute."):
                try:
                    report = analyze_contract(
                        text, doc_type, jurisdiction, reader_role, settings
                    )
                except Exception as exc:
                    st.error(explain_ai_error(exc))
                else:
                    st.session_state.report = report
                    masked_note = " · IDs masked before sending" if mask_on else " · sent unmasked"
                    st.session_state.report_source = (
                        f"Live analysis · {PROVIDERS[settings['provider']]['label']} · "
                        f"{settings['model']}{masked_note} · not legal advice"
                    )

    if st.session_state.report:
        render_report(st.session_state.report, st.session_state.report_source)


if __name__ == "__main__":
    main()
