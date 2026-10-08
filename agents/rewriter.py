import json
from utils.mistral_client import simple_chat

_SYSTEM = (
    "You are an expert CV writer. Tailor CVs to specific job postings. "
    "CRITICAL RULE: never add skills, experience, or achievements that are not already in the CV. "
    "Only reorder sections, rephrase bullets, and adjust emphasis."
)


def rewrite_cv(cv: str, job_info: dict, analysis: dict) -> str:
    """
    Return a markdown CV rewritten to better match the target job.
    Preserves all facts; only adjusts framing, order, and language.
    """
    strengths = [s.get("point", "") for s in analysis.get("strengths", [])[:5]]
    gaps = [w.get("point", "") for w in analysis.get("weaknesses", [])[:3]]
    opportunities = analysis.get("opportunities", [])[:4]

    prompt = f"""Rewrite the CV below to better match the job posting.

RULES (non-negotiable):
- Do NOT invent any skill, role, achievement, or date not present in the original CV
- DO move the most relevant experience to the top within each section
- DO rewrite the professional summary to target {job_info.get("company")} and the {job_info.get("role")} role
- DO rephrase bullet points to naturally echo the job's language and keywords
- DO quantify achievements where the data already exists in the CV
- Keep the same markdown structure

=== TARGET ROLE ===
Company: {job_info.get("company")}
Role: {job_info.get("role")}
Key requirements: {", ".join(job_info.get("requirements", [])[:10])}
Key responsibilities: {", ".join(job_info.get("responsibilities", [])[:8])}

=== WHAT TO EMPHASISE ===
Strengths already present: {json.dumps(strengths)}
Opportunities to leverage: {json.dumps(opportunities)}
Gaps to minimise (if existing content allows): {json.dumps(gaps)}

=== ORIGINAL CV ===
{cv}

Output the complete rewritten CV in markdown. No preamble, no explanation."""

    return simple_chat([{"role": "user", "content": prompt}], system=_SYSTEM)
