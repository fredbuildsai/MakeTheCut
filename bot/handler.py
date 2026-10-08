"""
Single-user state machine:

  idle                       → waiting for a job URL
  awaiting_paste             → scraping failed; waiting for user to paste job text
  awaiting_action            → fit analysis shown; waiting for: sharpen | research | chat | skip
  awaiting_action_researched → same but company research already done
  chatting                   → free-form multi-turn chat about the current job
  awaiting_sharpen_output    → CV + cover letter ready; waiting for: cv | letter | both | skip
  awaiting_document_format   → waiting for: pdf | docx
  awaiting_cv_photo          → waiting for a profile photo (or "skip") before generating CV PDF
"""

import time as _import_timer; _it0 = _import_timer.monotonic()
def _ti(label):
    print(f"    [{_import_timer.monotonic()-_it0:.1f}s] {label}", flush=True)

import io; _ti("io")
import json; _ti("json")
import asyncio; _ti("asyncio")
import logging; _ti("logging")
from contextlib import asynccontextmanager; _ti("contextlib")
from html import escape; _ti("html")
from pathlib import Path; _ti("pathlib")

from telegram import Update; _ti("telegram.Update")
from telegram.ext import ContextTypes; _ti("telegram.ext")
from telegram.constants import ChatAction, ParseMode; _ti("telegram.constants")

from config import AUTHORIZED_USER_ID, FIT_THRESHOLD, AGENT_CALL_TIMEOUT, CHAT_CALL_TIMEOUT; _ti("config")
from agents.scraper import is_url, scrape_url; _ti("agents.scraper")
from agents.analyzer import parse_job_posting, analyze_fit, research_company, ask_agent; _ti("agents.analyzer")
from agents.rewriter import rewrite_cv; _ti("agents.rewriter")
from agents.cover_letter import write_cover_letter; _ti("agents.cover_letter")
from utils.file_manager import load_cv, load_preferences, create_application_folder, save_file, append_record, load_records, append_qa, log_conversation; _ti("utils.file_manager")
from utils.mistral_client import simple_chat; _ti("utils.mistral_client")
from utils.pdf_generator import markdown_to_pdf_bytes, pdf_filename, has_image_refs; _ti("utils.pdf_generator")
from utils.docx_generator import markdown_to_docx_bytes, docx_filename; _ti("utils.docx_generator")
from utils.email_sender import send_application_email, is_configured as email_is_configured; _ti("utils.email_sender")
from models import ApplicationRecord, JobInfo, FitAnalysis, ScoreBreakdown, ContactInfo, Strength, Weakness, PreferenceAlignment, QARecord; _ti("models")

logger = logging.getLogger(__name__)

# ── In-memory state ───────────────────────────────────────────────────────────

_state: dict = {
    "status": "idle",
    "job_info": None,
    "analysis": None,
    "output_folder": None,
    "url": None,
    "record": None,
    "cv": None,
    "prefs": None,
    "rewritten_cv_text": None,
    "cover_letter_text": None,
    "pending_output": None,       # "cv" | "letter" | "both" — stored while awaiting photo
    "doc_format": None,           # "pdf" | "docx" — chosen format for document generation
    "cv_photo_bytes": None,
    "chat_history": [],           # list of {"role": "user"|"assistant", "content": str}
    "chat_system": None,          # system message built once on chat entry
    "pre_chat_status": None,      # state to return to when chat exits
    "email_context": None,        # stores (cv_text, letter_text, job_info, analysis) while awaiting address
    "email_with_attachments": False,  # whether to include PDF attachments in the email
}

_SHARPEN_WORDS  = {"yes", "y", "si", "sí", "oui", "ja", "ok", "sure", "yep", "yup", "sharpen"}
_RESEARCH_WORDS = {"research", "company", "recherche", "empresa"}
_CHAT_WORDS     = {"chat", "discuss", "talk", "question", "ask", "discuter", "preguntar"}
_CHAT_EXIT_WORDS = {"exit", "done", "quit", "bye", "stop", "fin", "salir", "end"}
_CV_WORDS       = {"cv", "resume", "rewrite", "rewritten"}
_LETTER_WORDS   = {"letter", "cover", "lettre"}
_BOTH_WORDS     = {"both", "all", "deux", "todo"}
_EMAIL_WORDS    = {"email", "mail", "send", "envoyer"}
_SKIP_WORDS     = {"skip", "no", "non", "without", "sans", "nophoto"}
_YES_WORDS      = {"yes", "y", "oui", "si", "sí", "ja", "ok", "yep", "yup", "sure"}
_NO_WORDS       = {"no", "n", "non", "nope", "skip", "without", "sans"}
_PDF_WORDS      = {"pdf"}
_DOCX_WORDS     = {"docx", "word", "doc"}

_CHAT_MAX_EXCHANGES = 10  # rolling window — oldest pair dropped beyond this


def _reset():
    _state.update({
        "status": "idle",
        "job_info": None,
        "analysis": None,
        "output_folder": None,
        "url": None,
        "record": None,
        "cv": None,
        "prefs": None,
        "rewritten_cv_text": None,
        "cover_letter_text": None,
        "pending_output": None,
        "doc_format": None,           # "pdf" | "docx"
        "cv_photo_bytes": None,
        "email_context": None,
        "email_with_attachments": False,
        "chat_history": [],
        "chat_system": None,
        "pre_chat_status": None,
    })


# ── Typing indicator ─────────────────────────────────────────────────────────

@asynccontextmanager
async def _typing(update: Update, label: str = "⏳"):
    """Send a temporary message AND keep the 'typing…' indicator alive until done."""
    msg = await update.message.reply_text(f"{label} …")

    async def _keep_typing():
        while True:
            try:
                await update.message.chat.send_action(ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)

    task = asyncio.create_task(_keep_typing())
    try:
        yield
    finally:
        task.cancel()
        try:
            await msg.delete()
        except Exception:
            pass


# ── Telegram helpers ──────────────────────────────────────────────────────────

async def _send(update: Update, html: str):
    """Send HTML message, splitting at 4 096-char Telegram limit."""
    MAX = 4_000  # slightly under the hard limit for safety
    if len(html) <= MAX:
        await update.message.reply_text(html, parse_mode=ParseMode.HTML)
        return
    chunks, buf = [], ""
    for line in html.split("\n"):
        candidate = (buf + "\n" + line) if buf else line
        if len(candidate) > MAX:
            chunks.append(buf)
            buf = line
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)


# ── Formatting helpers ────────────────────────────────────────────────────────

def _analysis_to_html(job_info: dict, analysis: dict) -> str:
    score = analysis.get("fit_score", 0)
    emoji = "🟢" if score >= 80 else ("🟡" if score >= FIT_THRESHOLD else "🔴")

    lines = [
        f"📋 <b>{escape(job_info.get('role', '?'))} @ {escape(job_info.get('company', '?'))}</b>",
        f"📍 {escape(str(job_info.get('location', '')))} · {escape(str(job_info.get('employment_type', '')))}",
        "",
        f"{emoji} <b>Fit score: {score}/100</b>",
        f"<i>{escape(analysis.get('score_rationale', ''))}</i>",
    ]

    bd = analysis.get("score_breakdown")
    if isinstance(bd, dict):
        lines += [
            "",
            "📊 <b>Breakdown</b>",
            f"• Must-haves: {bd.get('must_have_requirements', 0)}/40",
            f"• Experience &amp; seniority: {bd.get('experience_seniority', 0)}/30",
            f"• Preferences &amp; logistics: {bd.get('preferences_logistics', 0)}/20",
            f"• Responsibilities &amp; bonus: {bd.get('responsibilities_nice_to_have', 0)}/10",
        ]

    if analysis.get("dealbreakers"):
        lines += ["", "⛔ <b>Dealbreakers</b> <i>(score capped at 49)</i>"]
        for db in analysis["dealbreakers"]:
            lines.append(f"• {escape(db)}")

    if analysis.get("job_inconsistencies"):
        lines += ["", "🔀 <b>Job description inconsistencies</b>"]
        for inc in analysis["job_inconsistencies"]:
            lines.append(f"• {escape(inc)}")

    lines += ["", "✅ <b>Strengths</b>"]
    for s in analysis.get("strengths", [])[:5]:
        lines.append(f"• {escape(s.get('point', ''))}")

    lines += ["", "⚠️ <b>Weaknesses</b>"]
    for w in analysis.get("weaknesses", [])[:5]:
        lines.append(f"• {escape(w.get('point', ''))} <i>({escape(str(w.get('impact', '')))})</i>")

    if analysis.get("red_flags"):
        lines += ["", "🚩 <b>Red flags</b>"]
        for rf in analysis["red_flags"]:
            lines.append(f"• {escape(rf)}")

    lines += ["", "💡 <b>Opportunities</b>"]
    for opp in analysis.get("opportunities", [])[:4]:
        lines.append(f"• {escape(opp)}")

    lines += ["", "📝 <b>Recommendation</b>", escape(analysis.get("recommendation", ""))]

    if analysis.get("improvement_suggestions"):
        lines += ["", "🔧 <b>Suggestions</b>"]
        for sug in analysis.get("improvement_suggestions", [])[:4]:
            lines.append(f"• {escape(sug)}")

    return "\n".join(lines)


def _analysis_to_md(job_info: dict, analysis: dict) -> str:
    score = analysis.get("fit_score", 0)
    lines = [
        f"# Fit Analysis: {job_info.get('role')} at {job_info.get('company')}",
        "",
        f"**Fit score: {score}/100**",
        f"> {analysis.get('score_rationale', '')}",
        "",
    ]

    bd = analysis.get("score_breakdown")
    if isinstance(bd, dict):
        lines += [
            "## Score breakdown",
            "",
            f"- Must-have requirements: {bd.get('must_have_requirements', 0)}/40",
            f"- Experience & seniority: {bd.get('experience_seniority', 0)}/30",
            f"- Preferences & logistics: {bd.get('preferences_logistics', 0)}/20",
            f"- Responsibilities & nice-to-have: {bd.get('responsibilities_nice_to_have', 0)}/10",
            "",
        ]

    if analysis.get("dealbreakers"):
        lines += ["## Dealbreakers (score capped at 49)", ""]
        for db in analysis["dealbreakers"]:
            lines.append(f"- {db}")
        lines.append("")

    if analysis.get("job_inconsistencies"):
        lines += ["## Job description inconsistencies", ""]
        for inc in analysis["job_inconsistencies"]:
            lines.append(f"- {inc}")
        lines.append("")

    lines += ["## Strengths"]
    for s in analysis.get("strengths", []):
        lines.append(f"- **{s.get('point')}** — *{s.get('evidence', '')}*")

    lines += ["", "## Weaknesses"]
    for w in analysis.get("weaknesses", []):
        lines.append(f"- **{w.get('point')}** (Impact: *{w.get('impact', '')}*)")

    if analysis.get("red_flags"):
        lines += ["", "## Red flags"]
        for rf in analysis["red_flags"]:
            lines.append(f"- {rf}")

    lines += ["", "## Preference alignment"]
    for pa in analysis.get("preference_alignment", []):
        lines.append(f"- **{pa.get('preference')}**: {pa.get('match', '?')} — {pa.get('notes', '')}")

    lines += ["", "## Opportunities"]
    for opp in analysis.get("opportunities", []):
        lines.append(f"- {opp}")

    lines += ["", "## Recommendation", "", analysis.get("recommendation", ""), "", "## Improvement suggestions"]
    for sug in analysis.get("improvement_suggestions", []):
        lines.append(f"- {sug}")

    return "\n".join(lines)


# ── Record builder ───────────────────────────────────────────────────────────

def _build_record(job_info: dict, analysis: dict, research: str, url: str, folder: str) -> ApplicationRecord:
    contact = job_info.get("contact") or {}
    return ApplicationRecord(
        url=url,
        folder=folder,
        company_research=research or "",
        job_info=JobInfo(
            company=job_info.get("company", "Unknown"),
            role=job_info.get("role", "Unknown"),
            location=job_info.get("location", "Unknown"),
            employment_type=job_info.get("employment_type", "unknown"),
            salary=job_info.get("salary"),
            language=job_info.get("language", "en"),
            requirements=job_info.get("requirements", []),
            nice_to_have=job_info.get("nice_to_have", []),
            responsibilities=job_info.get("responsibilities", []),
            company_description=job_info.get("company_description", ""),
            contact=ContactInfo(
                name=contact.get("name"),
                email=contact.get("email"),
                other=contact.get("other"),
            ),
            application_deadline=job_info.get("application_deadline"),
            benefits=job_info.get("benefits", []),
        ),
        analysis=FitAnalysis(
            fit_score=analysis.get("fit_score", 0),
            score_rationale=analysis.get("score_rationale", ""),
            score_breakdown=(
                ScoreBreakdown(**analysis["score_breakdown"])
                if isinstance(analysis.get("score_breakdown"), dict) else None
            ),
            dealbreakers=analysis.get("dealbreakers", []),
            job_inconsistencies=analysis.get("job_inconsistencies", []),
            strengths=[Strength(**s) for s in analysis.get("strengths", []) if isinstance(s, dict)],
            weaknesses=[Weakness(**w) for w in analysis.get("weaknesses", []) if isinstance(w, dict)],
            preference_alignment=[
                PreferenceAlignment(**p) for p in analysis.get("preference_alignment", [])
                if isinstance(p, dict) and p.get("match") in ("yes", "no", "partial")
            ],
            red_flags=analysis.get("red_flags", []),
            opportunities=analysis.get("opportunities", []),
            recommendation=analysis.get("recommendation", ""),
            improvement_suggestions=analysis.get("improvement_suggestions", []),
        ),
    )


# ── Action prompt ─────────────────────────────────────────────────────────────

def _action_prompt(score: int, researched: bool) -> str:
    options = ["Reply <b>sharpen</b> (or <b>yes</b>) to tailor your CV and draft a cover letter"]
    if not researched:
        options.append("Reply <b>research</b> to fetch company info first")
    options.append("Reply <b>chat</b> to discuss this job with the AI")
    options.append("Reply anything else to skip")
    return (
        f"\n✨ Score <b>{score}/100</b> clears the {FIT_THRESHOLD} threshold.\n\n"
        + "\n".join(f"• {o}" for o in options)
    )


# ── Chat mode ─────────────────────────────────────────────────────────────────

def _build_chat_system() -> str:
    """Build the rich system message injected once at chat entry."""
    job_info = _state["job_info"] or {}
    analysis = _state["analysis"] or {}
    cv = (_state["cv"] or "")[:2_000]
    prefs = (_state["prefs"] or "")[:800]

    bd = analysis.get("score_breakdown") or {}
    strengths = "; ".join(s.get("point", "") for s in analysis.get("strengths", [])[:4])
    weaknesses = "; ".join(w.get("point", "") for w in analysis.get("weaknesses", [])[:4])
    red_flags = "; ".join(analysis.get("red_flags", []))
    dealbreakers = "; ".join(analysis.get("dealbreakers", []))
    inconsistencies = "; ".join(analysis.get("job_inconsistencies", []))

    return (
        "You are a frank, knowledgeable career advisor helping a candidate evaluate a specific job. "
        "You have the full job details, the candidate's fit analysis, CV, and preferences below. "
        "Be direct, specific, and honest — reference concrete details from the job or CV when relevant. "
        "Keep answers concise (2-4 sentences unless a longer answer is clearly warranted). "
        "Do not repeat the fit score or re-summarise the analysis unprompted — the candidate already saw it.\n\n"
        "=== JOB ===\n"
        f"Company: {job_info.get('company')} | Role: {job_info.get('role')} | "
        f"Location: {job_info.get('location')} | Type: {job_info.get('employment_type')} | "
        f"Salary: {job_info.get('salary', 'not specified')}\n"
        f"Requirements: {', '.join(job_info.get('requirements', []))}\n"
        f"Responsibilities: {', '.join(job_info.get('responsibilities', []))}\n"
        f"Benefits: {', '.join(job_info.get('benefits', []))}\n\n"
        "=== FIT ANALYSIS ===\n"
        f"Score: {analysis.get('fit_score', 0)}/100 — {analysis.get('score_rationale', '')}\n"
        f"Breakdown: must-haves {bd.get('must_have_requirements', 0)}/40 | "
        f"experience {bd.get('experience_seniority', 0)}/30 | "
        f"preferences {bd.get('preferences_logistics', 0)}/20 | "
        f"responsibilities {bd.get('responsibilities_nice_to_have', 0)}/10\n"
        f"Strengths: {strengths}\n"
        f"Weaknesses: {weaknesses}\n"
        + (f"Red flags: {red_flags}\n" if red_flags else "")
        + (f"Dealbreakers: {dealbreakers}\n" if dealbreakers else "")
        + (f"Job inconsistencies: {inconsistencies}\n" if inconsistencies else "")
        + f"\n=== CANDIDATE CV (excerpt) ===\n{cv}\n\n"
        f"=== CANDIDATE PREFERENCES ===\n{prefs}"
    )


async def _handle_chat_message(update: Update, text: str) -> None:
    word = text.strip().lower().split()[0] if text.strip() else ""

    if word in _CHAT_EXIT_WORDS:
        _state["status"] = _state["pre_chat_status"] or "awaiting_action"
        _state["chat_history"] = []
        _state["chat_system"] = None
        _state["pre_chat_status"] = None
        score = (_state["analysis"] or {}).get("fit_score", 0)
        researched = _state["status"] == "awaiting_action_researched"
        await _send(update, "💬 Chat ended.\n" + _action_prompt(score, researched))
        return

    history: list = _state["chat_history"]
    history.append({"role": "user", "content": text})

    # Trim to rolling window
    max_msgs = _CHAT_MAX_EXCHANGES * 2
    if len(history) > max_msgs:
        _state["chat_history"] = history[-max_msgs:]
        history = _state["chat_history"]

    async with _typing(update, "💬"):
        try:
            reply = await asyncio.wait_for(
                asyncio.to_thread(simple_chat, history, _state["chat_system"], 0.3),
                timeout=CHAT_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("chat simple_chat timed out after %ds", CHAT_CALL_TIMEOUT)
            await _send(
                update,
                f"⏱ No response within {CHAT_CALL_TIMEOUT} seconds — please try again.",
            )
            return
        except Exception as exc:
            logger.error("chat simple_chat error", exc_info=True)
            await _send(update, f"❌ Error: <code>{escape(str(exc))}</code>")
            return

    history.append({"role": "assistant", "content": reply})
    await _send(update, escape(reply))


# ── Core pipeline ─────────────────────────────────────────────────────────────

async def _run_pipeline(update: Update, content: str, url: str):
    """Parse → fit analysis → show results → ask user what to do next."""
    try:
        # 1. Parse
        logger.info("Pipeline start — url=%s content_len=%d", url, len(content))
        await _send(update, "📄 Reading and parsing the job posting...")
        async with _typing(update):
            job_info = await asyncio.to_thread(parse_job_posting, content, url)
        _state["job_info"] = job_info

        company = job_info.get("company", "Unknown")
        role = job_info.get("role", "Unknown")
        logger.info("Parsed job: %s @ %s", role, company)
        await _send(update, f"🔎 Found: <b>{escape(role)}</b> at <b>{escape(company)}</b>")

        # 2. Load profile
        logger.info("Loading CV and preferences")
        try:
            cv = await asyncio.to_thread(load_cv)
            prefs = await asyncio.to_thread(load_preferences)
        except FileNotFoundError as exc:
            logger.error("Profile file missing: %s", exc)
            await _send(update, f"❌ {escape(str(exc))}")
            _reset()
            return

        _state["cv"] = cv
        _state["prefs"] = prefs

        # 3. Fit analysis
        await _send(update, "📖 Reading your CV and preferences...")
        await _send(update, "🔍 Cross-checking job posting with your profile...")
        async with _typing(update):
            analysis = await asyncio.to_thread(analyze_fit, job_info, cv, prefs)
        logger.info("Fit analysis done: fit_score=%s", analysis.get("fit_score"))
        await _send(update, "⭐ Rating job fit...")
        _state["analysis"] = analysis

        # 4. Show results
        score = analysis.get("fit_score", 0)
        await _send(update, _analysis_to_html(job_info, analysis))

        # 5. Save (after display so the user sees results immediately)
        await _send(update, "💾 Saving evaluation...")
        folder = await asyncio.to_thread(create_application_folder, company, role)
        _state["output_folder"] = folder

        job_md = (
            f"# {role} at {company}\n\n"
            f"**URL:** {url}\n\n"
            f"## Job details\n\n```json\n{json.dumps(job_info, indent=2, ensure_ascii=False)}\n```"
        )
        await asyncio.to_thread(save_file, folder, "job_description.md", job_md)
        await asyncio.to_thread(save_file, folder, "analysis.md", _analysis_to_md(job_info, analysis))

        record = _build_record(job_info, analysis, "", url, folder.name)
        _state["record"] = record
        await asyncio.to_thread(append_record, record)

        if score >= FIT_THRESHOLD:
            _state["status"] = "awaiting_action"
            await _send(update, _action_prompt(score, researched=False))
        else:
            await _send(
                update,
                f"\n📉 Score <b>{score}/100</b> is below the {FIT_THRESHOLD} threshold.\n"
                f"Analysis saved to <code>{folder.name}</code>.",
            )
            _reset()

    except Exception as exc:
        logger.error("Pipeline error", exc_info=True)
        await _send(update, f"❌ Unexpected error: <code>{escape(str(exc))}</code>\n\nPlease try again.")
        _reset()


async def _handle_sharpen(update: Update):
    """Rewrite CV + draft cover letter."""
    job_info = _state["job_info"]
    analysis = _state["analysis"]
    folder: Path = _state["output_folder"]
    cv = _state["cv"]
    prefs = _state["prefs"]

    try:
        await _send(update, "📝 Rewriting your CV for this role...")
        async with _typing(update):
            rewritten = await asyncio.to_thread(rewrite_cv, cv, job_info, analysis)
        await asyncio.to_thread(save_file, folder, "cv_rewritten.md", rewritten)
        _state["rewritten_cv_text"] = rewritten  # store immediately — available for PDF even if next step fails

        await _send(update, "✉️ Drafting cover letter...")
        async with _typing(update):
            letter = await asyncio.to_thread(write_cover_letter, cv, job_info, analysis, prefs)
        await asyncio.to_thread(save_file, folder, "cover_letter.md", letter)
        _state["cover_letter_text"] = letter  # store immediately

        record: ApplicationRecord = _state.get("record")
        if record:
            record.cv_rewritten = True
            record.cover_letter_generated = True
            await asyncio.to_thread(append_record, record)

        _state["status"] = "awaiting_sharpen_output"

        email_line = (
            "• <b>email</b> — send both as PDFs to your configured address\n"
            if email_is_configured() else ""
        )
        await _send(
            update,
            "✅ <b>CV and cover letter are ready.</b>\n\n"
            "What would you like?\n\n"
            "• <b>cv</b> — download the rewritten CV as PDF\n"
            "• <b>letter</b> — download the cover letter as PDF\n"
            "• <b>both</b> — download both as PDFs\n"
            + email_line +
            "• anything else — skip",
        )
    except Exception as exc:
        logger.error("Sharpen error", exc_info=True)
        await _send(update, f"❌ Unexpected error: <code>{escape(str(exc))}</code>\n\nPlease try again.")
        _reset()


async def _generate_and_send_documents(update: Update) -> None:
    """Generate CV and/or cover letter in the chosen format and send them."""
    pending    = _state["pending_output"]
    fmt        = _state.get("doc_format") or "pdf"
    want_cv    = pending in ("cv", "both")
    want_letter= pending in ("letter", "both")
    photo      = _state["cv_photo_bytes"]

    rewritten_cv = _state["rewritten_cv_text"]
    cover_letter = _state["cover_letter_text"]

    if want_cv and not rewritten_cv:
        await _send(update, "⚠️ Rewritten CV is not available — please run sharpen again.")
        _reset()
        return
    if want_letter and not cover_letter:
        await _send(update, "⚠️ Cover letter is not available — please run sharpen again.")
        _reset()
        return

    job_info = _state["job_info"]
    company  = job_info.get("company", "Company")
    role     = job_info.get("role", "Role")
    from datetime import datetime as _dt
    date_str = _dt.now().strftime("%Y-%m-%d")

    is_pdf = fmt == "pdf"
    async with _typing(update, "📄 Generating documents…"):
        if want_cv:
            if is_pdf:
                data  = await asyncio.to_thread(markdown_to_pdf_bytes, rewritten_cv, photo)
                fname = pdf_filename(date_str, role, company, "CV")
            else:
                data  = await asyncio.to_thread(markdown_to_docx_bytes, rewritten_cv, photo)
                fname = docx_filename(date_str, role, company, "CV")
            await update.message.reply_document(
                document=io.BytesIO(data),
                filename=fname,
                caption="📄 Rewritten CV — fill in the placeholder fields before sending.",
            )

        if want_letter:
            if is_pdf:
                data  = await asyncio.to_thread(markdown_to_pdf_bytes, cover_letter, None)
                fname = pdf_filename(date_str, role, company, "CoverLetter")
            else:
                data  = await asyncio.to_thread(markdown_to_docx_bytes, cover_letter, None)
                fname = docx_filename(date_str, role, company, "CoverLetter")
            await update.message.reply_document(
                document=io.BytesIO(data),
                filename=fname,
                caption="✉️ Cover letter — personalise the tone if needed.",
            )

    await _send(
        update,
        "💡 <b>Before sending:</b>\n"
        "• Fill in <code>[Your Name]</code>, <code>[Your Email]</code>, etc.\n"
        "• Review the tailored CV — confirm everything is factually accurate.",
    )
    _reset()


import re as _re
_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def _prompt_for_email(
    update: Update,
    cv_text: str | None,
    letter_text: str | None,
    job_info: dict,
    analysis: dict,
) -> None:
    """Ask whether to include PDF attachments, then ask for the recipient address."""
    _state["email_context"] = (cv_text, letter_text, job_info, analysis)

    _state["status"] = "awaiting_email_attachments"
    if cv_text or letter_text:
        docs = []
        if cv_text:
            docs.append("tailored CV")
        if letter_text:
            docs.append("cover letter")
        await _send(
            update,
            f"📎 Attach {' and '.join(docs)} to the email?\n"
            "Reply <b>PDF</b>, <b>Word</b> (docx), or <b>no</b> for analysis only.",
        )
    else:
        await _send(update, "📎 No documents available — reply <b>no</b> to send analysis only, or cancel.")


async def _ask_email_address(update: Update) -> None:
    _state["status"] = "awaiting_email_address"
    from config import RESEND_TO
    hint = f" (or press Enter to use <code>{escape(RESEND_TO)}</code>)" if RESEND_TO else ""
    await _send(update, f"📧 What email address should I send this to?{hint}")


async def _handle_email_attachments(update: Update, text: str) -> None:
    word = text.strip().lower().split()[0] if text.strip() else ""
    if word in _NO_WORDS:
        _state["email_with_attachments"] = False
        _state["doc_format"] = None
        await _ask_email_address(update)
    elif word in _PDF_WORDS or word in _YES_WORDS:
        _state["email_with_attachments"] = True
        _state["doc_format"] = "pdf"
        await _ask_email_address(update)
    elif word in _DOCX_WORDS:
        _state["email_with_attachments"] = True
        _state["doc_format"] = "docx"
        await _ask_email_address(update)
    else:
        await _send(update, "Please reply <b>PDF</b>, <b>Word</b> (docx), or <b>no</b> to send analysis only.")


async def _do_send_email(update: Update, to_address: str) -> None:
    """Generate PDFs if requested and send analysis + attachments."""
    cv_text, letter_text, job_info, analysis = _state["email_context"]
    with_attachments = _state.get("email_with_attachments", False)
    role    = job_info.get("role", "Role")
    company = job_info.get("company", "Company")
    score   = analysis.get("fit_score", 0)

    from datetime import datetime as _dt
    date_str = _dt.now().strftime("%Y-%m-%d")
    photo = _state.get("cv_photo_bytes")

    try:
        attachments = []
        if with_attachments:
            fmt = _state.get("doc_format") or "pdf"
            is_pdf = fmt == "pdf"
            if cv_text:
                label = "CV PDF" if is_pdf else "CV (Word)"
                async with _typing(update, f"📄 Generating {label}…"):
                    if is_pdf:
                        data  = await asyncio.to_thread(markdown_to_pdf_bytes, cv_text, photo)
                        fname = pdf_filename(date_str, role, company, "CV")
                    else:
                        data  = await asyncio.to_thread(markdown_to_docx_bytes, cv_text, photo)
                        fname = docx_filename(date_str, role, company, "CV")
                attachments.append((fname, data))
            if letter_text:
                label = "cover letter PDF" if is_pdf else "cover letter (Word)"
                async with _typing(update, f"📄 Generating {label}…"):
                    if is_pdf:
                        data  = await asyncio.to_thread(markdown_to_pdf_bytes, letter_text, None)
                        fname = pdf_filename(date_str, role, company, "CoverLetter")
                    else:
                        data  = await asyncio.to_thread(markdown_to_docx_bytes, letter_text, None)
                        fname = docx_filename(date_str, role, company, "CoverLetter")
                attachments.append((fname, data))

        async with _typing(update, "📧 Sending email…"):
            await asyncio.to_thread(
                send_application_email, role, company, score, attachments, job_info, analysis, to_address
            )
    except Exception as exc:
        logger.error("Email send error", exc_info=True)
        await _send(update, f"❌ Failed to send email: <code>{escape(str(exc))}</code>")
        _state["status"] = "awaiting_email_address"
        return

    attach_note = f" with {len(attachments)} PDF{'s' if len(attachments) > 1 else ''}" if attachments else ""
    await _send(update, f"📧 Sent{attach_note} to <code>{escape(to_address)}</code>.")
    _state["email_context"] = None
    _state["email_with_attachments"] = False
    _reset()


async def _handle_email_address(update: Update, text: str) -> None:
    """Validate the user-provided email address and send, or re-prompt."""
    address = text.strip()

    # Allow the user to confirm with the default RESEND_TO address if blank/enter
    if not address:
        from config import RESEND_TO
        if RESEND_TO:
            address = RESEND_TO
        else:
            await _send(update, "Please type a valid email address.")
            return

    if not _EMAIL_RE.match(address):
        await _send(update, f"⚠️ <code>{escape(address)}</code> doesn't look like a valid email. Please try again.")
        return

    await _do_send_email(update, address)


async def _email_application(
    update: Update,
    cv_text: str | None = None,
    letter_text: str | None = None,
    job_info: dict | None = None,
    analysis: dict | None = None,
) -> None:
    """Resolve documents from state or arguments, then ask for a recipient address."""
    if not email_is_configured():
        await _send(update, "⚠️ Email not configured. Set <code>RESEND_API_KEY</code> in your .env file.")
        return

    cv_text     = cv_text     or _state.get("rewritten_cv_text")
    letter_text = letter_text or _state.get("cover_letter_text")
    job_info    = job_info    or _state.get("job_info") or {}
    analysis    = analysis    or _state.get("analysis") or {}

    await _prompt_for_email(update, cv_text, letter_text, job_info, analysis)


async def _handle_sharpen_output(update: Update, text: str):
    """Send CV and/or cover letter as PDFs based on user choice."""
    word = text.strip().lower().split()[0] if text.strip() else ""

    if word in _EMAIL_WORDS:
        await _email_application(update)
        return

    want_cv = word in _CV_WORDS or word in _BOTH_WORDS
    want_letter = word in _LETTER_WORDS or word in _BOTH_WORDS

    if not want_cv and not want_letter:
        await _send(update, "👍 Skipping. Files are saved in your applications folder.")
        _reset()
        return

    pending = "both" if (want_cv and want_letter) else ("cv" if want_cv else "letter")
    _state["pending_output"] = pending
    _state["status"] = "awaiting_document_format"
    await _send(update, "📄 Which format? Reply <b>PDF</b> or <b>Word</b> (docx).")


async def _handle_document_format(update: Update, text: str) -> None:
    word = text.strip().lower().split()[0] if text.strip() else ""
    if word in _PDF_WORDS:
        _state["doc_format"] = "pdf"
    elif word in _DOCX_WORDS:
        _state["doc_format"] = "docx"
    else:
        await _send(update, "Please reply <b>PDF</b> or <b>Word</b>.")
        return

    want_cv = _state["pending_output"] in ("cv", "both")
    if want_cv and _state["doc_format"] == "pdf" and has_image_refs(_state["rewritten_cv_text"] or ""):
        _state["status"] = "awaiting_cv_photo"
        await _send(
            update,
            "🖼 Your CV references a profile photo.\n\n"
            "Send me a photo or image file to include it, "
            "or reply <b>skip</b> to generate the document without one.",
        )
        return

    await _generate_and_send_documents(update)


async def _handle_cv_photo(update: Update, photo_bytes: bytes | None) -> None:
    """Called when user sends a photo or replies 'skip' while in awaiting_cv_photo."""
    _state["cv_photo_bytes"] = photo_bytes
    await _generate_and_send_documents(update)


async def _handle_research(update: Update):
    """Fetch company research, update saved files, then ask about sharpening."""
    job_info = _state["job_info"]
    folder: Path = _state["output_folder"]

    try:
        await _send(update, "🌐 Fetching company data...")
        research, research_error = "", None
        async with _typing(update):
            try:
                research, research_error = await asyncio.wait_for(
                    asyncio.to_thread(research_company, job_info),
                    timeout=AGENT_CALL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning("research_company timed out after %ds", AGENT_CALL_TIMEOUT)
                research_error = f"timed out after {AGENT_CALL_TIMEOUT}s"

        if not research:
            logger.warning("Company research failed or returned empty: %s", research_error)
            await _send(
                update,
                f"⚠️ Company research unavailable — {escape(str(research_error or 'no data returned'))}.\n"
                "Continuing without it.",
            )
        else:
            # Update job_description.md with research
            url = _state.get("url", "")
            company = job_info.get("company", "Unknown")
            role = job_info.get("role", "Unknown")
            job_md = (
                f"# {role} at {company}\n\n"
                f"**URL:** {url}\n\n"
                f"## Job details\n\n```json\n{json.dumps(job_info, indent=2, ensure_ascii=False)}\n```\n\n"
                f"## Company research\n\n{research}"
            )
            await asyncio.to_thread(save_file, folder, "job_description.md", job_md)

            record: ApplicationRecord = _state.get("record")
            if record:
                record.company_research = research
                await asyncio.to_thread(append_record, record)

            await _send(update, f"🏢 <b>Company research</b>\n\n{escape(research[:3_000])}")

        score = _state["analysis"].get("fit_score", 0)
        _state["status"] = "awaiting_action_researched"
        await _send(update, _action_prompt(score, researched=True))

    except Exception as exc:
        logger.error("Research error", exc_info=True)
        await _send(update, f"❌ Unexpected error: <code>{escape(str(exc))}</code>\n\nPlease try again.")
        _reset()


# ── Message router ────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for every text message."""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name or ""
    chat_id = update.effective_chat.id
    message_id = update.message.message_id
    text = (update.message.text or "").strip()

    logger.info("Message from user_id=%d status=%s", user_id, _state["status"])



    # Fire-and-forget CSV logging — don't block the response path
    asyncio.create_task(asyncio.to_thread(log_conversation, user_id, username, chat_id, message_id, text))

    status = _state["status"]
    word = text.lower().split()[0] if text else ""

    # ── Awaiting PDF attachment choice
    if status == "awaiting_email_attachments":
        await _handle_email_attachments(update, text)
        return

    # ── Awaiting email address
    if status == "awaiting_email_address":
        await _handle_email_address(update, text)
        return

    # ── Awaiting PDF output choice after sharpen
    if status == "awaiting_sharpen_output":
        await _handle_sharpen_output(update, text)
        return

    # ── Awaiting document format choice (pdf vs docx)
    if status == "awaiting_document_format":
        await _handle_document_format(update, text)
        return

    # ── Awaiting profile photo (user replied "skip" with text)
    if status == "awaiting_cv_photo":
        if text.strip().lower().split()[0] in _SKIP_WORDS:
            await _handle_cv_photo(update, None)
        else:
            await _send(
                update,
                "Please send a photo or image file, or reply <b>skip</b> to continue without one.",
            )
        return

    # ── Active chat session
    if status == "chatting":
        await _handle_chat_message(update, text)
        return

    # ── Awaiting action after fit analysis
    if status in ("awaiting_action", "awaiting_action_researched"):
        if word in _SHARPEN_WORDS:
            await _handle_sharpen(update)
        elif word in _RESEARCH_WORDS and status == "awaiting_action":
            await _handle_research(update)
        elif word in _CHAT_WORDS:
            _state["pre_chat_status"] = status
            _state["chat_history"] = []
            _state["chat_system"] = _build_chat_system()
            _state["status"] = "chatting"
            score = (_state["analysis"] or {}).get("fit_score", 0)
            await _send(
                update,
                f"💬 <b>Chat mode — {escape((_state['job_info'] or {}).get('role', 'this role'))} "
                f"@ {escape((_state['job_info'] or {}).get('company', ''))}</b>\n\n"
                "Ask me anything about this job, the fit, salary, culture, risks, or how to position yourself.\n\n"
                "Reply <b>exit</b> when you're done to return to your options.",
            )
        else:
            await _send(update, "👍 Skipping. Analysis is saved.\n\nSend me another job URL whenever you're ready.")
            _reset()
        return

    # ── Waiting for manual paste after failed scrape
    if status == "awaiting_paste":
        if len(text) > 300:
            await _send(update, "✅ Got it. Analysing the pasted description...")
            await _run_pipeline(update, text, _state.get("url", "manual"))
        else:
            await _send(
                update,
                "⚠️ That looks too short for a full job description. "
                "Please paste the complete text of the posting.",
            )
        return

    # ── Idle: expect a URL
    if is_url(text):
        _state["status"] = "processing"
        await _send(update, "🔍 Fetching job posting...")
        logger.info("Scraping URL: %s", text)
        success, content = await asyncio.to_thread(scrape_url, text)
        if success:
            logger.info("Scrape result: success=True content_len=%d", len(content))
            await _run_pipeline(update, content, text)
        else:
            logger.info("Scrape result: success=False reason=%s", content)
            _state["status"] = "awaiting_paste"
            _state["url"] = text
            await _send(
                update,
                "⚠️ I wasn't able to fetch that page automatically (the site may block bots or require a login).\n\n"
                "Please open the job posting in your browser, select all the text, and paste it here.",
            )
    else:
        await _send(
            update,
            "👋 Send me a job posting URL and I'll score the fit with your CV.\n\n"
            "Once you've filled in <code>profile/cv.md</code> and <code>profile/preferences.md</code>, "
            "just drop any job URL here.",
        )


async def handle_list(update: Update, context: ContextTypes.DEFAULT_TYPE):

    records = await asyncio.to_thread(load_records)
    if not records:
        await _send(update, "No analyses saved yet. Send a job URL to get started.")
        return

    text_mode = context.args and context.args[0].lower() == "text"

    if text_mode:
        W_SCORE = 5
        W_COMPANY = 22
        W_ROLE = 28
        header = f"{'Score':>{W_SCORE}}  {'Company':<{W_COMPANY}}  {'Role':<{W_ROLE}}"
        divider = "-" * len(header)
        rows = [header, divider]
        for r in records:
            emoji = "🟢" if r.analysis.fit_score >= 80 else ("🟡" if r.analysis.fit_score >= FIT_THRESHOLD else "🔴")
            rows.append(
                f"{emoji}{str(r.analysis.fit_score):>{W_SCORE - 1}}  "
                f"{r.job_info.company[:W_COMPANY]:<{W_COMPANY}}  "
                f"{r.job_info.role[:W_ROLE]:<{W_ROLE}}"
            )
        date_range = f"{records[-1].timestamp.strftime('%Y-%m-%d')} → {records[0].timestamp.strftime('%Y-%m-%d')}"
        await _send(update, f"📋 <b>{len(records)} analyses</b> ({date_range})\n\n<pre>{chr(10).join(rows)}</pre>")
    else:
        from utils.table_image import render_table
        buf = await asyncio.to_thread(render_table, records, FIT_THRESHOLD)
        await update.message.reply_photo(photo=buf)


async def handle_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):

    question = " ".join(context.args).strip() if context.args else ""
    if not question:
        await _send(update, "Usage: <code>/ask your question here</code>")
        return

    logger.info("handle_ask question: %s", question)
    records = await asyncio.to_thread(load_records)
    try:
        cv    = await asyncio.to_thread(load_cv)
        prefs = await asyncio.to_thread(load_preferences)
    except FileNotFoundError:
        cv = prefs = ""
    answer: str | None = None
    async with _typing(update):
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(ask_agent, question, records, cv, prefs),
                timeout=CHAT_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("handle_ask timed out after %ds", CHAT_CALL_TIMEOUT)
            await _send(
                update,
                f"⏱ No response within {CHAT_CALL_TIMEOUT} seconds — please try again.",
            )
            return
        except Exception as exc:
            logger.error("handle_ask error", exc_info=True)
            await _send(update, f"❌ Could not get an answer: <code>{escape(str(exc))}</code>")
            return

    await _send(update, answer)

    qa = QARecord(question=question, answer=answer)
    await asyncio.to_thread(append_qa, qa)
    logger.info("Q&A saved: id=%s", qa.id)


async def handle_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the most recently sharpened job application materials via email."""

    if not email_is_configured():
        await _send(update, "⚠️ Email not configured. Set <code>RESEND_API_KEY</code> and <code>RESEND_TO</code> in your .env file.")
        return

    from config import APPLICATIONS_DIR

    # Always load from disk so we have the texts even after _reset() cleared in-memory state
    records = await asyncio.to_thread(load_records)
    record = next(iter(records), None)
    if not record:
        await _send(update, "📭 No analysis found. Send a job URL first.")
        return

    cv_text = letter_text = None
    if record.cv_rewritten or record.cover_letter_generated:
        folder = APPLICATIONS_DIR / record.folder
        cv_path     = folder / "cv_rewritten.md"
        letter_path = folder / "cover_letter.md"
        if cv_path.exists():
            cv_text = cv_path.read_text(encoding="utf-8")
        if letter_path.exists():
            letter_text = letter_path.read_text(encoding="utf-8")

    # Prefer fresher in-memory texts if sharpen was run in this session and not yet reset
    cv_text     = _state.get("rewritten_cv_text") or cv_text
    letter_text = _state.get("cover_letter_text") or letter_text

    job_info = record.job_info.model_dump()
    analysis = record.analysis.model_dump()
    await _email_application(update, cv_text, letter_text, job_info, analysis)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(
        update,
        "📖 <b>Job Fit Bot — Help</b>\n\n"
        "Stop wasting time on jobs that aren't a real fit. Send a URL, get an honest score, "
        "then sharpen your application where it counts.\n\n"

        "━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>Commands</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"

        "/start — show the welcome message\n"
        "/help — show this help\n"
        "/list — table of all analysed jobs\n"
        "  └ <code>/list text</code> — plain text instead of image\n"
        "/ask &lt;question&gt; — ask a question across all your saved analyses\n"
        "  └ e.g. <code>/ask which job had the best salary?</code>\n"
        "/email — email the most recent sharpened CV + cover letter via Resend\n\n"

        "━━━━━━━━━━━━━━━━\n"
        "🚀 <b>Basic flow</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"

        "1. Send a job posting URL.\n"
        "2. If scraping fails, paste the job text directly.\n"
        "3. Review the fit score and breakdown.\n"
        "4. Reply with one of:\n"
        "   • <b>sharpen</b> — tailor your CV + draft a cover letter\n"
        "   • <b>research</b> — fetch company info first\n"
        "   • <b>chat</b> — free-form discussion about the job\n"
        "   • anything else — skip and move on\n\n"

        "━━━━━━━━━━━━━━━━\n"
        "💬 <b>Chat mode</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"

        "Enter with <b>chat</b> after an analysis. Ask anything — salary, risks, culture, "
        "how to position yourself. Reply <b>exit</b> to return to your options.\n\n"

        "━━━━━━━━━━━━━━━━\n"
        "📄 <b>PDF output</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"

        "After sharpening, reply:\n"
        "• <b>cv</b> — download rewritten CV as PDF\n"
        "• <b>letter</b> — download cover letter as PDF\n"
        "• <b>both</b> — download both\n\n"

        "━━━━━━━━━━━━━━━━\n"
        "🗂 <b>Scoring</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"

        "Score is built from 4 components:\n"
        "• Must-have requirements — 40 pts\n"
        "• Experience &amp; seniority — 30 pts\n"
        "• Preferences &amp; logistics — 20 pts\n"
        "• Responsibilities &amp; nice-to-haves — 10 pts\n\n"
        "Hard dealbreakers cap the score at 49 regardless of other strengths.\n\n"

        f"Threshold to unlock sharpen: <b>{FIT_THRESHOLD}/100</b>",
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle an incoming photo or image document when the bot is awaiting a CV profile photo."""

    if _state["status"] != "awaiting_cv_photo":
        return  # ignore photos in any other state

    try:
        if update.message.photo:
            # Telegram compresses photos; grab the highest-res version.
            file = await update.message.photo[-1].get_file()
        elif update.message.document and update.message.document.mime_type.startswith("image/"):
            file = await update.message.document.get_file()
        else:
            await _send(update, "Please send an image file, or reply <b>skip</b> to continue without one.")
            return

        photo_buf = io.BytesIO()
        await file.download_to_memory(photo_buf)
        await _handle_cv_photo(update, photo_buf.getvalue())
    except Exception as exc:
        logger.error("Photo download error", exc_info=True)
        await _send(update, f"⚠️ Could not read the photo: <code>{escape(str(exc))}</code>. Reply <b>skip</b> to continue without one.")


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all state and return to idle — unsticks any hanging flow."""
    prev = _state.get("status", "idle")
    _reset()
    logger.info("handle_reset: cleared state (was: %s)", prev)
    await _send(update, "🔄 Reset. State cleared — send a job URL to start again.")


async def handle_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show what the bot is waiting for; cancel and reset states that are hard to escape."""
    status = _state.get("status", "idle")
    logger.info("handle_continue: status=%s", status)

    # States where /continue acts as a cancel (nothing useful to continue with)
    cancellable = {
        "awaiting_paste":          "Scraping had failed and I was waiting for a manual paste.",
        "awaiting_cv_photo":       "I was waiting for a CV photo.",
        "awaiting_email_address":  "I was waiting for an email address.",
        "awaiting_email_attachments": "I was waiting for an attachment choice.",
        "awaiting_document_format":"I was waiting for a format choice.",
    }
    if status in cancellable:
        reason = cancellable[status]
        _reset()
        await _send(update, f"↩️ {reason} Cancelled — state cleared. Send a job URL to start again.")
        return

    messages = {
        "idle":                       "Ready — send me a job URL.",
        "processing":                 "⏳ Still processing — please wait a moment.",
        "awaiting_action":            "Reply <b>sharpen</b>, <b>research</b>, <b>chat</b>, or <b>skip</b>.",
        "awaiting_action_researched": "Reply <b>sharpen</b>, <b>chat</b>, or <b>skip</b>.",
        "chatting":                   "💬 Chat mode active. Ask a question or reply <b>exit</b> to leave.",
        "awaiting_sharpen_output":    "Reply <b>cv</b>, <b>letter</b>, <b>both</b>, <b>email</b>, or <b>skip</b>.",
    }
    msg = messages.get(status, f"Status: <code>{status}</code> — use /reset to clear if stuck.")
    await _send(update, f"ℹ️ {msg}")


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send(
        update,
        "👋 <b>Job Fit Bot</b>\n\n"
        "Drop a job posting URL and I will:\n"
        "1. Fetch and parse the job description\n"
        "2. Research the company online\n"
        "3. Score the fit against your CV and preferences (0 – 100)\n"
        f"4. If score ≥ <b>{FIT_THRESHOLD}</b>: offer to rewrite your CV and draft a cover letter\n\n"
        "📁 <b>Before you start, make sure you have:</b>\n"
        "• <code>profile/cv.md</code> — your current CV\n"
        "• <code>profile/preferences.md</code> — what you're looking for\n\n"
        "💡 To find your Telegram user ID, message @userinfobot\n\n"
        "Ready — send a URL!",
    )
