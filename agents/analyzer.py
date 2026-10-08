import json
import logging
from utils.mistral_client import simple_chat, agent_chat
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from models import ApplicationRecord

logger = logging.getLogger(__name__)

_PARSE_SYSTEM = (
    "You are an expert HR analyst. Extract structured information from job postings. "
    "Respond with ONLY valid JSON — no markdown fences, no preamble, no explanation."
)

_ANALYZE_SYSTEM = (
    "You are a strict, evidence-bound technical recruiter screening a candidate against a job. "
    "Score ONLY on facts present in the CV and posting — never assume, infer, or give benefit of "
    "the doubt for skills not explicitly evidenced. Unstated means absent. A missing required "
    "qualification is a gap, not a maybe. Most real candidate/job pairings are a partial match: "
    "be sparing with high scores and reserve 80+ for genuinely strong fits. "
    "Respond with ONLY valid JSON — no markdown fences, no preamble, no explanation."
)


def _strip_json(text: str) -> dict:
    """Parse JSON, tolerating ```json ... ``` fences."""
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("```")
        t = parts[1] if len(parts) > 1 else t
        if t.startswith("json"):
            t = t[4:]
    return json.loads(t.strip())


# ── 1. Parse job posting ──────────────────────────────────────────────────────

def parse_job_posting(content: str, url: str) -> dict:
    """Extract structured job info from raw posting text."""
    prompt = f"""Extract information from the job posting below. Return JSON matching this schema exactly:
{{
  "company": "string",
  "role": "string",
  "location": "string",
  "employment_type": "full-time | part-time | contract | freelance | unknown",
  "salary": "string or null",
  "language": "2-letter ISO code of the posting language (en/fr/es/de/it/pt/nl/...)",
  "requirements": ["must-have skill or qualification"],
  "nice_to_have": ["bonus skill or qualification"],
  "responsibilities": ["key responsibility"],
  "company_description": "brief description of the company from the posting",
  "contact": {{
    "name": "hiring manager name or null",
    "email": "contact email or null",
    "other": "any other contact info or null"
  }},
  "application_deadline": "deadline string or null",
  "benefits": ["benefit"]
}}

URL: {url}
CONTENT:
{content[:12_000]}"""

    raw = simple_chat([{"role": "user", "content": prompt}], system=_PARSE_SYSTEM)
    logger.info("parse_job_posting raw response: %s", raw[:500])
    try:
        return _strip_json(raw)
    except Exception:
        logger.warning("parse_job_posting: JSON parse failed, using fallback")
        return {
            "company": "Unknown",
            "role": "Unknown",
            "location": "Unknown",
            "employment_type": "unknown",
            "salary": None,
            "language": "en",
            "requirements": [],
            "nice_to_have": [],
            "responsibilities": [],
            "company_description": content[:400],
            "contact": {"name": None, "email": None, "other": None},
            "application_deadline": None,
            "benefits": [],
        }


# ── 2. Fit analysis ───────────────────────────────────────────────────────────

_COMPONENT_CAPS = {
    "must_have_requirements": 40,
    "experience_seniority": 30,
    "preferences_logistics": 20,
    "responsibilities_nice_to_have": 10,
}
_DEALBREAKER_CAP = 49


def _enforce_scoring_rules(result: dict) -> dict:
    """Recompute fit_score from the breakdown and apply the dealbreaker cap deterministically.

    The model is asked to do this arithmetic, but we redo it so the score is a rule, not a guess.
    """
    breakdown = result.get("score_breakdown")
    if isinstance(breakdown, dict):
        # Clamp each component to its allowed range, then sum to a composite.
        composite = 0
        for key, cap in _COMPONENT_CAPS.items():
            try:
                val = int(breakdown.get(key, 0) or 0)
            except (TypeError, ValueError):
                val = 0
            val = max(0, min(val, cap))
            breakdown[key] = val
            composite += val
    else:
        # No breakdown available (e.g. older response shape) — fall back to the model's number.
        try:
            composite = int(result.get("fit_score", 0) or 0)
        except (TypeError, ValueError):
            composite = 0
        composite = max(0, min(composite, 100))

    dealbreakers = result.get("dealbreakers") or []
    final = min(composite, _DEALBREAKER_CAP) if dealbreakers else composite

    if final != result.get("fit_score"):
        logger.info(
            "analyze_fit: adjusted fit_score %s → %s (composite=%s, dealbreakers=%d)",
            result.get("fit_score"), final, composite, len(dealbreakers),
        )
    result["fit_score"] = final
    return result


def analyze_fit(job_info: dict, cv: str, preferences: str) -> dict:
    """Score and analyse the fit between the candidate and the job. Uses simple_chat — no web search."""
    job_summary = (
        f"Company: {job_info.get('company')}\n"
        f"Role: {job_info.get('role')}\n"
        f"Location: {job_info.get('location')}\n"
        f"Type: {job_info.get('employment_type')}\n"
        f"Salary: {job_info.get('salary', 'not specified')}\n"
        f"Requirements: {', '.join(job_info.get('requirements', []))}\n"
        f"Nice to have: {', '.join(job_info.get('nice_to_have', []))}\n"
        f"Responsibilities: {', '.join(job_info.get('responsibilities', []))}\n"
        f"Benefits: {', '.join(job_info.get('benefits', []))}"
    )

    prompt = f"""Score the fit between the candidate and this job using the rubric below.

=== SCORING RUBRIC ===
Score each component independently, using ONLY evidence in the CV and posting.
Award partial points proportionally (e.g. 5 of 8 required skills clearly met → ~25/40).
Do NOT credit a skill unless it is explicitly evidenced — unstated means absent.

  must_have_requirements      (0-40): required skills, qualifications, certifications, years of experience, language
  experience_seniority        (0-30): domain/industry relevance and seniority match (penalise under- AND over-qualification)
  preferences_logistics       (0-20): location, remote/onsite, employment type, salary, and the candidate's own stated preferences
  responsibilities_nice_to_have (0-10): match to day-to-day responsibilities plus any bonus/nice-to-have skills

composite = sum of the four components (0-100).

=== JOB INCONSISTENCIES ===
Scan the job description for internal contradictions or conflicting signals that would be
impossible or very difficult to satisfy simultaneously. Examples:
  - "entrepreneurial mindset" alongside "must follow established processes and governance"
  - "fully remote" but "regular on-site collaboration required"
  - "senior/autonomous" yet "reports to three stakeholders with weekly approval gates"
  - "fast-paced startup" but "5+ years of enterprise experience required"
List each as a short "X vs. Y" statement. Leave empty if the posting is internally consistent.
These are observations about the job itself — they are not gaps in the candidate's profile.

=== DEALBREAKERS ===
List as "dealbreakers" any HARD blocker that should disqualify regardless of other strengths, e.g.:
  - a requirement the posting marks as mandatory/required that the candidate definitively does NOT meet
  - a hard preference conflict (e.g. on-site required in a location the candidate cannot work)
Soft concerns are NOT dealbreakers — put those in "red_flags" and reflect them in the component scores instead.

=== FINAL SCORE ===
If "dealbreakers" is non-empty, set fit_score = min(composite, 49).
Otherwise fit_score = composite.

Score bands (for your calibration — be honest):
  ≤40 Poor · 41-55 Weak · 56-69 Below bar · 70-79 Moderate · 80-89 Good · 90-100 Excellent

Return JSON matching this schema exactly:
{{
  "score_breakdown": {{
    "must_have_requirements": <0-40>,
    "experience_seniority": <0-30>,
    "preferences_logistics": <0-20>,
    "responsibilities_nice_to_have": <0-10>
  }},
  "fit_score": <integer 0-100, computed per the rules above>,
  "score_rationale": "1-2 sentences justifying the score, referencing the components and any dealbreakers",
  "dealbreakers": ["hard blocker"],
  "job_inconsistencies": ["contradictory or conflicting signal in the job description itself, e.g. 'flexible mindset vs. strict process adherence', 'startup agility vs. enterprise compliance'"],
  "strengths": [
    {{"point": "strength description", "evidence": "specific evidence from the CV"}}
  ],
  "weaknesses": [
    {{"point": "gap or weakness", "impact": "how much this matters for the role"}}
  ],
  "preference_alignment": [
    {{"preference": "candidate preference", "match": "yes | no | partial", "notes": "explanation"}}
  ],
  "red_flags": ["serious concern that is not a hard dealbreaker"],
  "opportunities": ["angle to emphasise or opportunity to leverage"],
  "recommendation": "2-3 sentence overall recommendation",
  "improvement_suggestions": ["specific thing the candidate could do before applying"]
}}

=== JOB ===
{job_summary}

=== CANDIDATE CV ===
{cv[:4_000]}

=== CANDIDATE PREFERENCES ===
{preferences[:1_500]}"""

    raw = simple_chat([{"role": "user", "content": prompt}], system=_ANALYZE_SYSTEM, temperature=0)
    logger.info("analyze_fit raw response: %s", raw[:500])
    try:
        result = _strip_json(raw)
        return _enforce_scoring_rules(result)
    except Exception:
        logger.warning("analyze_fit: JSON parse failed, using fallback")
        return {
            "fit_score": 0,
            "score_rationale": "Analysis could not be parsed — please try again.",
            "score_breakdown": {
                "must_have_requirements": 0,
                "experience_seniority": 0,
                "preferences_logistics": 0,
                "responsibilities_nice_to_have": 0,
            },
            "dealbreakers": [],
            "job_inconsistencies": [],
            "strengths": [],
            "weaknesses": [],
            "preference_alignment": [],
            "red_flags": ["Parsing error"],
            "opportunities": [],
            "recommendation": "Please try again.",
            "improvement_suggestions": [],
        }


# ── 3. Company research (optional — agent with web search) ────────────────────

def research_company(job_info: dict) -> str:
    """Research the company via the Mistral agent. Returns a summary string, or empty string on failure."""
    company = job_info.get("company", "this company")
    role = job_info.get("role", "this role")
    description = job_info.get("company_description", "")[:600]

    prompt = f"""Research {company} and their open {role} position.

Company description from the job posting:
{description}

Search for and summarise:
1. Company size, culture, values, and work environment
2. Recent news (funding, product launches, layoffs, challenges)
3. Industry positioning and main competitors
4. What employees say about working there (Glassdoor, LinkedIn, etc.)

Provide a concise, honest summary in 3-4 paragraphs."""

    try:
        result = agent_chat(prompt)
        logger.info("research_company raw response: %s", result[:500])
        return result, None
    except Exception as exc:
        logger.warning("research_company failed: %s", exc)
        return "", str(exc)


# ── Follow-up Q&A ─────────────────────────────────────────────────────────────

_MAX_CV_CHARS    = 3000   # trim very long CVs to keep prompt size manageable
_MAX_RECORDS     = 15    # max analyses to include (most recent, one per unique role+company)

def ask_agent(question: str, records: list, cv: str = "", preferences: str = "") -> str:
    """Answer a free-form question with full context: CV, preferences, and saved analyses."""
    sections = []

    if cv:
        cv_trimmed = cv[:_MAX_CV_CHARS] + ("\n…[truncated]" if len(cv) > _MAX_CV_CHARS else "")
        sections.append(f"## Candidate CV\n{cv_trimmed}")
    if preferences:
        sections.append(f"## Job Search Preferences\n{preferences}")

    if records:
        # Deduplicate: keep the most recent analysis per (company, role) pair
        seen: set = set()
        deduped = []
        for r in records:
            key = (r.job_info.company.lower(), r.job_info.role.lower())
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        deduped = deduped[:_MAX_RECORDS]

        summaries = []
        for r in deduped:
            analysis = r.analysis
            strengths  = "; ".join(s.point for s in (analysis.strengths  or [])[:2])
            weaknesses = "; ".join(w.point for w in (analysis.weaknesses or [])[:2])
            red_flags  = "; ".join((analysis.red_flags or [])[:2])
            summaries.append(
                f"- {r.job_info.role} @ {r.job_info.company}: "
                f"score={analysis.fit_score}/100 | {analysis.recommendation} | "
                f"strengths: {strengths} | gaps: {weaknesses}"
                + (f" | flags: {red_flags}" if red_flags else "")
            )
        sections.append(f"## Saved Job Analyses ({len(deduped)} unique jobs)\n" + "\n".join(summaries))
    else:
        sections.append("## Saved Job Analyses\nNo analyses saved yet.")

    context = "\n\n".join(sections)

    system = (
        "You are a career advisor with full access to the candidate's CV, job search preferences, "
        "and their saved job fit analyses. Answer questions honestly and specifically, "
        "drawing on the data provided. Be concise."
    )

    try:
        answer = simple_chat(
            messages=[{"role": "user", "content": f"{context}\n\n## Question\n{question}"}],
            system=system,
        )
        logger.info("ask_agent response: %s", answer[:500])
        return answer
    except Exception as exc:
        logger.warning("ask_agent failed: %s", exc)
        raise
