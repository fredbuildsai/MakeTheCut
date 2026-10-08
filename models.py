from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Job posting ───────────────────────────────────────────────────────────────

class ContactInfo(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    other: Optional[str] = None


class JobInfo(BaseModel):
    company: str
    role: str
    location: str
    employment_type: Literal["full-time", "part-time", "contract", "freelance", "unknown"]
    salary: Optional[str] = None
    language: str = "en"
    requirements: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    company_description: str = ""
    contact: ContactInfo = Field(default_factory=ContactInfo)
    application_deadline: Optional[str] = None
    benefits: list[str] = Field(default_factory=list)


# ── Fit analysis ──────────────────────────────────────────────────────────────

class Strength(BaseModel):
    point: str
    evidence: str = ""


class Weakness(BaseModel):
    point: str
    impact: str = ""


class PreferenceAlignment(BaseModel):
    preference: str
    match: Literal["yes", "no", "partial"]
    notes: str = ""


class ScoreBreakdown(BaseModel):
    """Component scores that sum to the composite fit_score (before any dealbreaker cap)."""
    must_have_requirements: int = Field(default=0, ge=0, le=40)   # required skills, quals, certs, years, language
    experience_seniority: int = Field(default=0, ge=0, le=30)     # domain relevance, under/over-qualified
    preferences_logistics: int = Field(default=0, ge=0, le=20)    # location, remote/onsite, type, salary, stated prefs
    responsibilities_nice_to_have: int = Field(default=0, ge=0, le=10)  # day-to-day match + bonus skills


class FitAnalysis(BaseModel):
    fit_score: int = Field(ge=0, le=100)
    score_rationale: str
    score_breakdown: Optional[ScoreBreakdown] = None
    dealbreakers: list[str] = Field(default_factory=list)
    job_inconsistencies: list[str] = Field(default_factory=list)
    strengths: list[Strength] = Field(default_factory=list)
    weaknesses: list[Weakness] = Field(default_factory=list)
    preference_alignment: list[PreferenceAlignment] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    recommendation: str
    improvement_suggestions: list[str] = Field(default_factory=list)


# ── Q&A ──────────────────────────────────────────────────────────────────────

class QARecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    question: str
    answer: str


# ── Top-level record ──────────────────────────────────────────────────────────

class ApplicationRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    url: str
    folder: str
    job_info: JobInfo
    analysis: FitAnalysis
    company_research: str = ""
    cv_rewritten: bool = False
    cover_letter_generated: bool = False
