import json
from utils.mistral_client import simple_chat

_LANG_NAMES: dict[str, str] = {
    "en": "English", "fr": "French", "es": "Spanish", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "sv": "Swedish", "da": "Danish", "nb": "Norwegian", "fi": "Finnish",
    "ru": "Russian", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "ar": "Arabic", "tr": "Turkish", "ca": "Catalan",
}


def write_cover_letter(cv: str, job_info: dict, analysis: dict, preferences: str) -> str:
    """
    Draft a cover letter in the language of the job posting.
    Uses the candidate's top strengths and the job contact info (if available).
    """
    lang_code = job_info.get("language", "en")
    lang_name = _LANG_NAMES.get(lang_code, "English")

    contact = job_info.get("contact") or {}
    contact_name = (contact.get("name") or "").strip()
    contact_email = (contact.get("email") or "").strip()

    salutation = f"Dear {contact_name}," if contact_name else "Dear Hiring Team,"
    contact_line = f"Attn: {contact_name}" if contact_name else ""

    strengths_text = json.dumps(
        [
            {"point": s.get("point", ""), "evidence": s.get("evidence", "")}
            for s in analysis.get("strengths", [])[:4]
        ],
        indent=2,
        ensure_ascii=False,
    )

    prompt = f"""Write a professional cover letter in {lang_name}.

INSTRUCTIONS:
- Language: {lang_name} throughout
- Length: exactly 3-4 paragraphs — concise and specific
- Opening paragraph: a compelling hook specific to {job_info.get("company")} and the {job_info.get("role")} role; never start with "I am writing to apply"
- Middle paragraph(s): reference 2-3 concrete strengths with evidence from the CV; tie them directly to the role's requirements
- Closing paragraph: express genuine interest, invite an interview, state availability
- Tone: professional but warm — authentic, not corporate boilerplate; avoid hollow phrases like "team player" or "passionate about"

=== JOB ===
Company: {job_info.get("company")}
Role: {job_info.get("role")}
Location: {job_info.get("location")}
Top requirements: {", ".join(job_info.get("requirements", [])[:8])}

=== CANDIDATE STRENGTHS FOR THIS ROLE ===
{strengths_text}

=== CANDIDATE CV (excerpt) ===
{cv[:2_500]}

=== CANDIDATE VALUES & PREFERENCES ===
{preferences[:800]}

FORMAT — output the letter in this markdown structure, nothing else:
---
[Your Name]
[Your Address]
[Your Email] | [Your Phone]
[Date]

{job_info.get("company", "[Company]")}
{contact_line}
{contact_email}

{salutation}

[3-4 paragraphs of the letter]

Kind regards,
[Your Name]
---"""

    system = (
        f"You are an expert cover letter writer. Write compelling, personalised letters in {lang_name}. "
        "Be specific and genuine. Never use clichés."
    )
    return simple_chat([{"role": "user", "content": prompt}], system=system)
