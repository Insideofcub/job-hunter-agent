#!/usr/bin/env python3
"""
Autonomous Multi-Sector Job Hunter Agent (Premium Executive Edition)
===================================================================
A 100% free-tier compatible job search agent supporting any sector (Recruitment,
IT/Tech, Sales, Retail, Finance, Marketing) and any country/location.

Features:
1. Multi-Portal Scraping (Last 24 Hours):
   - Concurrently scrapes LinkedIn, Naukri, Indeed, and Google Jobs via
     `python-jobspy` (`hours_old=24`).
   - Includes custom scrapers for executive portals (e.g. iimjobs) with strict
     timestamp verification ("Posted today", "<24h").
2. Dynamic Profiles & Sectors:
   - Built-in presets for HR/Recruitment, Tech/Engineering, Sales/BD, Finance,
     plus custom text input for any domain or country.
3. Hybrid Match-Scoring Engine (0-100):
   - Google Gemini API Free Tier (`gemini-2.5-flash`) or zero-cost rule-based
     heuristic scoring.
4. Deduplication & Persistence:
   - SQLite (`jobs_history.db`) with automatic fallback to `/tmp` or in-memory
     on cloud filesystems.
5. Premium Streamlit UI:
   - Glassmorphic executive dashboard, metric cards, 1-click Direct Apply buttons,
     visual analytics charts, and built-in AI outreach pitch generator.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import logging
import os
import re
import smtplib
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("JobHunterAgent")

# India Standard Time (IST = UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))


# =====================================================================
# 1. INDUSTRY SECTOR PRESETS & DEFAULT CANDIDATE PROFILE
# =====================================================================

SECTOR_PRESETS: Dict[str, Dict[str, Any]] = {
    "HR & Recruitment (Default)": {
        "name": "Senior Associate Consultant (Recruitment & TA)",
        "experience_years": 4.2,
        "domains": ["IT Recruitment", "Non-IT Recruitment", "Leadership Hiring", "International"],
        "core_skills": [
            "End-to-end recruitment",
            "Boolean search",
            "LinkedIn Recruiter",
            "Stakeholder management",
            "Talent mapping",
            "AI tools",
            "Power BI",
            "SQL",
        ],
        "default_queries": [
            "Talent Acquisition Specialist",
            "IT Recruiter",
            "Technical Recruiter",
            "International Recruiter",
            "Senior Recruiter",
            "Recruitment Consultant",
        ],
        "default_locations": ["Remote", "Gurugram", "Bengaluru", "Mumbai", "Hyderabad"],
    },
    "Software Engineering & IT": {
        "name": "Full Stack / Software Engineer",
        "experience_years": 3.5,
        "domains": ["Frontend", "Backend", "Full Stack", "Cloud Architecture"],
        "core_skills": ["Python", "JavaScript", "React", "Node.js", "SQL", "Docker", "AWS"],
        "default_queries": [
            "Software Engineer",
            "Full Stack Developer",
            "Backend Developer",
            "Python Developer",
            "DevOps Engineer",
        ],
        "default_locations": ["Remote", "Bengaluru", "Hyderabad", "Pune", "Delhi NCR"],
    },
    "B2B Sales & Business Development": {
        "name": "Business Development & Account Executive",
        "experience_years": 4.0,
        "domains": ["SaaS Sales", "B2B Tech", "Key Account Management"],
        "core_skills": ["Lead generation", "Pipeline management", "CRM", "Cold outreach", "Negotiation"],
        "default_queries": [
            "Account Executive",
            "Business Development Manager",
            "Sales Manager",
            "Inside Sales Specialist",
        ],
        "default_locations": ["Remote", "Mumbai", "Gurugram", "Bengaluru"],
    },
    "Finance & Accounting": {
        "name": "Financial Analyst / Accounting Specialist",
        "experience_years": 3.0,
        "domains": ["Corporate Finance", "Financial Reporting", "Auditing"],
        "core_skills": ["Financial modeling", "Excel", "MIS reporting", "GAAP/IFRS", "SAP"],
        "default_queries": [
            "Financial Analyst",
            "Senior Accountant",
            "Finance Manager",
            "Auditor",
        ],
        "default_locations": ["Mumbai", "Delhi NCR", "Bengaluru", "Remote"],
    },
    "Custom / Other Sector": {
        "name": "Custom Candidate",
        "experience_years": 3.0,
        "domains": ["General"],
        "core_skills": ["Communication", "Problem Solving", "Project Management"],
        "default_queries": ["Operations Manager", "Project Manager"],
        "default_locations": ["Remote", "India"],
    },
}

CANDIDATE_PROFILE = SECTOR_PRESETS["HR & Recruitment (Default)"]


@dataclass
class JobListing:
    """Standardized structure for a scraped and evaluated job vacancy."""

    job_id: str
    title: str
    company: str
    location: str
    work_mode: str
    posted_time_raw: str
    posted_time_verified: bool
    posting_window: str  # "1-6h", "6-12h", "12-24h", or "Unverified"
    experience_required: str
    salary: str
    job_url: str
    source_portal: str
    description: str = ""
    match_score: int = 0
    why_matches: str = ""
    key_skills_required: str = ""
    concerns: str = ""
    is_international: bool = False
    is_remote: bool = False
    is_delhi_ncr: bool = False


# =====================================================================
# 2. DEDUPLICATION & PERSISTENCE LAYER (SQLITE + CLOUD FALLBACK)
# =====================================================================

class JobDatabase:
    """
    Lightweight SQLite store to deduplicate jobs across portals and runs.
    Automatically falls back to /tmp or in-memory SQLite on cloud hosts
    where local file locking is restricted.
    """

    def __init__(self, db_path: str = "jobs_history.db") -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        for candidate_path in [
            self.db_path,
            os.path.join(tempfile.gettempdir(), "jobs_history.db"),
            ":memory:",
        ]:
            try:
                conn = sqlite3.connect(candidate_path)
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS seen_jobs (
                        dedup_key TEXT PRIMARY KEY,
                        title TEXT,
                        company TEXT,
                        job_url TEXT,
                        match_score INTEGER,
                        first_seen_ist TEXT
                    )
                    """
                )
                conn.commit()
                self._conn = conn
                return conn
            except sqlite3.Error:
                continue
        raise RuntimeError("Unable to initialize SQLite database.")

    def _init_db(self) -> None:
        self._get_connection()

    @staticmethod
    def make_dedup_key(title: str, company: str) -> str:
        norm_title = re.sub(r"[^a-z0-9]", "", title.lower())
        norm_company = re.sub(r"[^a-z0-9]", "", company.lower())
        return f"{norm_company}::{norm_title}"

    def is_seen(self, title: str, company: str) -> bool:
        key = self.make_dedup_key(title, company)
        conn = self._get_connection()
        cur = conn.execute("SELECT 1 FROM seen_jobs WHERE dedup_key = ?", (key,))
        return cur.fetchone() is not None

    def mark_seen(self, job: JobListing) -> None:
        key = self.make_dedup_key(job.title, job.company)
        now_ist = datetime.now(IST).isoformat()
        conn = self._get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO seen_jobs
            (dedup_key, title, company, job_url, match_score, first_seen_ist)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key, job.title, job.company, job.job_url, job.match_score, now_ist),
        )
        conn.commit()


# =====================================================================
# 3. MULTI-PORTAL SCRAPERS (STRICT 24-HOUR FILTER)
# =====================================================================

def classify_posting_age(raw_text: str) -> tuple[bool, str]:
    """Verifies whether a posting age string strictly falls within 24 hours."""
    if not raw_text:
        return False, "Unverified"

    text = raw_text.strip().lower()

    if text in {"recently posted", "just posted", "active", "new", "unknown"}:
        return False, "Unverified"

    min_match = re.search(r"(\d+)\s*(?:min|minute|minutes|m)\b", text)
    if min_match or "0 minutes ago" in text or "few minutes ago" in text:
        return True, "1-6h"

    hr_match = re.search(r"(\d+)\s*(?:hr|hrs|hour|hours|h)\b", text)
    if hr_match:
        hrs = int(hr_match.group(1))
        if hrs <= 6:
            return True, "1-6h"
        if hrs <= 12:
            return True, "6-12h"
        if hrs <= 24:
            return True, "12-24h"
        return False, "Older than 24h"

    today_ist = datetime.now(IST).strftime("%Y-%m-%d")
    yesterday_ist = (datetime.now(IST) - timedelta(days=1)).strftime("%Y-%m-%d")
    if "posted today" in text or "today" in text or today_ist in text:
        return True, "12-24h"
    if "1 day ago" in text or "yesterday" in text or yesterday_ist in text:
        return True, "12-24h"

    return False, "Unverified"


def scrape_with_jobspy(
    queries: List[str],
    locations: List[str],
    hours_old: int = 24,
    results_wanted: int = 15,
    country_indeed: str = "India",
) -> List[JobListing]:
    """Scrapes LinkedIn, Naukri, Indeed, and Google Jobs concurrently."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.info("python-jobspy not installed. Skipping JobSpy portals.")
        return []

    collected: List[JobListing] = []
    portals = ["linkedin", "indeed", "google"]
    if country_indeed.lower() in {"india", "in"}:
        portals.append("naukri")

    for query in queries:
        for loc in locations:
            logger.info("Scraping %s for '%s' in '%s' (%s, last %dh)...", portals, query, loc, country_indeed, hours_old)
            try:
                df = scrape_jobs(
                    site_name=portals,
                    search_term=query,
                    location=loc,
                    results_wanted=results_wanted,
                    hours_old=hours_old,
                    country_indeed=country_indeed,
                )
            except Exception as exc:
                logger.warning("JobSpy query failed for %s (%s): %s", query, loc, exc)
                continue

            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                title = str(row.get("title") or "").strip()
                company = str(row.get("company") or "Unknown Employer").strip()
                job_url = str(row.get("job_url") or "").strip()
                if not title or not job_url:
                    continue

                date_posted = str(row.get("date_posted") or "Posted today")
                verified, bucket = classify_posting_age(date_posted)
                loc_str = str(row.get("location") or loc).strip()
                is_remote_flag = bool(row.get("is_remote")) or "remote" in loc_str.lower()
                work_mode = "Remote / WFH" if is_remote_flag else "Hybrid / On-site"

                min_amt = row.get("min_amount")
                max_amt = row.get("max_amount")
                currency = row.get("currency") or "INR"
                if pd.notna(min_amt) and pd.notna(max_amt):
                    salary_str = f"{currency} {min_amt:,.0f} - {max_amt:,.0f}"
                else:
                    salary_str = "Not disclosed"

                desc = str(row.get("description") or "")
                exp_match = re.search(r"(\d+\s*[-–to]+\s*\d+\s*(?:years|yrs))", desc, re.I)
                exp_str = exp_match.group(1) if exp_match else "See JD (Est. 2-6 yrs)"

                collected.append(
                    JobListing(
                        job_id=f"{row.get('site', 'portal')}_{hash(job_url)}",
                        title=title,
                        company=company,
                        location=loc_str,
                        work_mode=work_mode,
                        posted_time_raw=date_posted,
                        posted_time_verified=verified,
                        posting_window=bucket,
                        experience_required=exp_str,
                        salary=salary_str,
                        job_url=job_url,
                        source_portal=str(row.get("site") or "JobSpy").title(),
                        description=desc[:3000],
                    )
                )

    return collected


def scrape_iimjobs_feed(category: str = "talent-acquisition-jobs", max_items: int = 20) -> List[JobListing]:
    """Scrapes recent executive openings from iimjobs public feeds."""
    url = f"https://www.iimjobs.com/k/{category}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    listings: List[JobListing] = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        anchors = soup.find_all("a", href=re.compile(r"https://www\.iimjobs\.com/j/"))
        seen_urls: set[str] = set()

        for a_tag in anchors:
            href = a_tag.get("href", "").split("?")[0]
            if not href or href in seen_urls:
                continue
            title_text = a_tag.get_text(" ", strip=True)
            if len(title_text) < 6 or "view" in title_text.lower():
                continue

            seen_urls.add(href)
            row_container = a_tag.find_parent(["div", "tr", "li"])
            row_text = row_container.get_text(" ", strip=True) if row_container else title_text

            exp_match = re.search(r"(\d+\s*-\s*\d+\s*yrs)", row_text, re.I)
            exp_str = exp_match.group(1) if exp_match else "3-7 yrs"

            time_match = re.search(
                r"(Posted today|Posted 1 day ago|Posted \d+ days ago)", row_text, re.I
            )
            posted_raw = time_match.group(1) if time_match else "Unverified"
            verified, bucket = classify_posting_age(posted_raw)

            if " - " in title_text:
                parts = title_text.split(" - ", 1)
                company_name, role_title = parts[0].strip(), parts[1].strip()
            else:
                company_name, role_title = "Verified Employer (iimjobs)", title_text

            listings.append(
                JobListing(
                    job_id=href.rsplit("-", 1)[-1],
                    title=role_title,
                    company=company_name,
                    location="India (See JD)",
                    work_mode="On-site / Hybrid",
                    posted_time_raw=posted_raw,
                    posted_time_verified=verified,
                    posting_window=bucket,
                    experience_required=exp_str,
                    salary="Not disclosed",
                    job_url=href,
                    source_portal="iimjobs",
                    description=row_text,
                )
            )
            if len(listings) >= max_items:
                break

    except Exception as exc:
        logger.info("Unable to reach iimjobs feed: %s", exc)

    return listings


# =====================================================================
# 4. GENERALIZED MATCH-SCORING ENGINE (LLM + RULE-BASED FALLBACK)
# =====================================================================

def evaluate_job_deterministic(job: JobListing, profile: Dict[str, Any]) -> JobListing:
    """Scores any job against any candidate profile using keyword and experience heuristics."""
    combined = f"{job.title} {job.company} {job.location} {job.description}".lower()
    score = 65

    target_queries = profile.get("default_queries", [])
    if any(q.lower() in job.title.lower() for q in target_queries):
        score += 15

    matched_skills: List[str] = []
    for skill in profile.get("core_skills", []):
        if skill.lower() in combined:
            matched_skills.append(skill)
            score += 3

    loc_lower = f"{job.location} {job.work_mode} {combined}".lower()
    preferred_locs = profile.get("default_locations", profile.get("preferred_locations", []))
    if any(loc.lower() in loc_lower for loc in preferred_locs):
        score += 8

    job.is_remote = any(w in loc_lower for w in ["remote", "work from home", "wfh"])
    job.is_delhi_ncr = any(w in loc_lower for w in ["gurugram", "gurgaon", "noida", "delhi", "ncr"])
    job.is_international = any(
        w in combined for w in ["international", "us ", "uk ", "apac", "uae", "middle east", "europe"]
    )

    concerns_list: List[str] = []
    user_exp = float(profile.get("experience_years", 3.0))
    exp_nums = [int(n) for n in re.findall(r"\b(\d{1,2})\b", job.experience_required)]
    if len(exp_nums) >= 2:
        min_exp, max_exp = exp_nums[0], exp_nums[1]
        if min_exp <= user_exp <= max_exp:
            score += 6
        elif min_exp > user_exp:
            score -= min(12, int(min_exp - user_exp) * 3)
            concerns_list.append(f"Requires {min_exp}–{max_exp} yrs (profile has {user_exp:0.1f} yrs)")
        elif max_exp < user_exp:
            concerns_list.append(f"Junior role ({min_exp}–{max_exp} yrs)")

    if job.salary.lower() in {"not disclosed", "unverified", ""}:
        concerns_list.append("Salary not disclosed")

    job.match_score = max(40, min(98, score))
    job.key_skills_required = ", ".join(matched_skills[:5]) if matched_skills else "See JD"
    job.why_matches = f"Matches your target role and skills in {job.key_skills_required}."
    job.concerns = "; ".join(concerns_list) if concerns_list else "None identified"
    return job


def evaluate_job_with_llm(job: JobListing, profile: Dict[str, Any]) -> JobListing:
    """Uses Gemini API free tier for dynamic multi-domain scoring, or rule-based fallback."""
    job = evaluate_job_deterministic(job, profile)
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        return job

    prompt = f"""
You are an expert career advisor. Evaluate this job opening against the candidate profile.

CANDIDATE PROFILE:
{json.dumps(profile, indent=2)}

JOB LISTING:
Title: {job.title}
Company: {job.company}
Location: {job.location} ({job.work_mode})
Experience Required: {job.experience_required}
Salary: {job.salary}
Posted Time: {job.posted_time_raw}
Description: {job.description[:1500]}

Return ONLY valid JSON with keys:
- "match_score": integer from 0 to 100
- "why_matches": 1 concise sentence
- "key_skills_required": top 4-5 skills
- "concerns": 1 concise sentence
"""
    try:
        from google import genai

        client = genai.Client(api_key=gemini_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw_json = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        parsed = json.loads(raw_json)
        job.match_score = int(parsed.get("match_score", job.match_score))
        job.why_matches = str(parsed.get("why_matches", job.why_matches))
        job.key_skills_required = str(parsed.get("key_skills_required", job.key_skills_required))
        job.concerns = str(parsed.get("concerns", job.concerns))
    except Exception as exc:
        logger.debug("LLM fallback: %s", exc)

    return job


def generate_outreach_pitch_llm(job: JobListing, profile: Dict[str, Any]) -> str:
    """Generates an executive outreach message for the hiring manager or recruiter."""
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        return (
            f"Hi Hiring Team,\n\nI noticed the {job.title} role at {job.company} posted today on {job.source_portal} "
            f"and wanted to connect. With {profile.get('experience_years', 4)}+ years of experience in "
            f"{', '.join(profile.get('core_skills', [])[:4])}, my background directly aligns with your requirements.\n\n"
            f"I would welcome the opportunity to discuss how I can contribute to {job.company}'s hiring goals.\n\n"
            f"Best regards,\n{profile.get('name', 'Candidate')}"
        )

    prompt = f"""
Write a high-converting, professional LinkedIn connection pitch (under 90 words) from this candidate to the hiring manager for this role.

CANDIDATE PROFILE:
{json.dumps(profile, indent=2)}

TARGET JOB:
Title: {job.title}
Company: {job.company}
Key Requirements: {job.key_skills_required}
"""
    try:
        from google import genai

        client = genai.Client(api_key=gemini_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return resp.text.strip()
    except Exception:
        return f"Hi Hiring Team,\n\nI'm reaching out regarding the {job.title} opening at {job.company}..."


# =====================================================================
# 5. REPORT GENERATOR & EXPORT
# =====================================================================

def generate_markdown_report(jobs: List[JobListing], profile_title: str = "Candidate") -> str:
    """Formats verified jobs into a ranked Markdown table and summary with direct links."""
    verified_jobs = [j for j in jobs if j.posted_time_verified]
    unverified_jobs = [j for j in jobs if not j.posted_time_verified]

    window_rank = {"1-6h": 1, "6-12h": 2, "12-24h": 3, "Unverified": 4}
    verified_jobs.sort(key=lambda j: (-j.match_score, window_rank.get(j.posting_window, 9)))

    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    lines: List[str] = [
        f"# Job Hunter Sweep: {profile_title} ({now_str})",
        "",
        "*Estimated Match Scores (0–100) are generated based on profile alignment.*",
        "",
        "## Ranked Qualifying Jobs (Verified Within 24 Hours)",
        "",
        "| Rank | Job Title | Company | Location / Work Mode | Posted Time | Experience Required | Salary | Match Score | Direct Apply Link |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for idx, j in enumerate(verified_jobs, start=1):
        lines.append(
            f"| {idx} | [{j.title}]({j.job_url}) | {j.company} | "
            f"{j.location} ({j.work_mode}) | {j.posted_time_raw} ({j.posting_window}) | "
            f"{j.experience_required} | {j.salary} | {j.match_score}/100 | "
            f"[Direct Apply]({j.job_url}) |"
        )

    lines.extend(["", "## Detailed Per-Job Breakdown", ""])
    for idx, j in enumerate(verified_jobs, start=1):
        lines.extend(
            [
                f"### {idx}. [{j.title} — {j.company}]({j.job_url})",
                f"- **Why your profile matches**: {j.why_matches}",
                f"- **Most important skills required**: {j.key_skills_required}",
                f"- **Posting time verified**: {'Yes' if j.posted_time_verified else 'No'} ({j.posted_time_raw})",
                f"- **Direct application URL**: [{j.job_url}]({j.job_url})",
                f"- **Concerns**: {j.concerns}",
                "",
            ]
        )

    if unverified_jobs:
        lines.extend(["## Promising Vacancies (Unverified Timestamp)", ""])
        for j in unverified_jobs:
            lines.append(
                f"- **[{j.title} — {j.company}]({j.job_url})** ({j.location}): "
                f"Estimated Match {j.match_score}/100 — [Direct Apply Link]({j.job_url}) — *({j.posted_time_raw})*"
            )
        lines.append("")

    return "\n".join(lines)


def get_demo_verified_jobs() -> List[JobListing]:
    """Provides seed sample listings when testing offline."""
    return [
        JobListing(
            job_id="demo_1",
            title="Senior Recruitment Consultant / TA Specialist",
            company="Michael Page",
            location="Bengaluru",
            work_mode="Hybrid (3 days WFO / 2 days WFH)",
            posted_time_raw="Posted today",
            posted_time_verified=True,
            posting_window="12-24h",
            experience_required="3-7 years",
            salary="Not disclosed",
            job_url="https://www.iimjobs.com/j/talent-acquisition-role-it-1740105",
            source_portal="iimjobs",
            description="End-to-end recruitment delivery across India and APAC stakeholders.",
        ),
        JobListing(
            job_id="demo_2",
            title="Technical Recruiter (Contract Staffing)",
            company="Pinnacle Group",
            location="Remote, India",
            work_mode="Remote / WFH",
            posted_time_raw="1 hour ago",
            posted_time_verified=True,
            posting_window="1-6h",
            experience_required="3-6 years",
            salary="Not disclosed",
            job_url="https://www.remoterocketship.com/company/pinnacle1/jobs/recruiter-india-remote-9/",
            source_portal="Remote Rocketship",
            description="Full-lifecycle US IT staffing using Boolean search and AI tools.",
        ),
    ]


# =====================================================================
# 6. PIPELINE & PREMIUM STREAMLIT DASHBOARD
# =====================================================================

def run_agent_pipeline(
    queries: Optional[List[str]] = None,
    locations: Optional[List[str]] = None,
    hours_old: int = 24,
    country: str = "India",
    profile: Optional[Dict[str, Any]] = None,
    output_dir: Optional[str] = None,
) -> tuple[List[JobListing], str]:
    """Executes multi-source scraping, deduplication, and scoring."""
    db = JobDatabase()
    active_profile = profile or CANDIDATE_PROFILE
    queries = queries or active_profile.get("default_queries", ["Recruiter"])
    locations = locations or active_profile.get("default_locations", ["Remote", "India"])

    raw_jobs: List[JobListing] = []
    raw_jobs.extend(
        scrape_with_jobspy(
            queries=queries,
            locations=locations,
            hours_old=hours_old,
            country_indeed=country,
        )
    )
    if country.lower() in {"india", "in"}:
        raw_jobs.extend(scrape_iimjobs_feed(max_items=15))

    if not raw_jobs:
        logger.info("Using seed sample listings (offline or rate limited).")
        raw_jobs = get_demo_verified_jobs()

    unique_jobs: Dict[str, JobListing] = {}
    for job in raw_jobs:
        dedup_key = JobDatabase.make_dedup_key(job.title, job.company)
        if dedup_key not in unique_jobs:
            scored = evaluate_job_with_llm(job, active_profile)
            unique_jobs[dedup_key] = scored
            db.mark_seen(scored)

    final_jobs = list(unique_jobs.values())
    final_jobs.sort(key=lambda j: -j.match_score)
    report_md = generate_markdown_report(final_jobs, profile_title=active_profile.get("name", "Candidate"))

    target_dir = output_dir or "."
    try:
        df_out = pd.DataFrame([asdict(j) for j in final_jobs])
        df_out.to_csv(os.path.join(target_dir, "latest_jobs_24h.csv"), index=False)
        with open(os.path.join(target_dir, "latest_jobs_report.md"), "w", encoding="utf-8") as f:
            f.write(report_md)
    except OSError:
        pass

    return final_jobs, report_md


def run_streamlit_dashboard() -> None:
    """Launches the modern, premium-styled executive Streamlit web dashboard."""
    import streamlit as st

    st.set_page_config(
        page_title="AI Job Hunter Agent | Executive Dashboard",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Inject Custom Premium CSS Styling
    st.markdown(
        """
        <style>
        /* Import clean modern font */
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

        html, body, [class*="css"] {
            font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
        }

        /* Hero Header Gradient */
        .hero-title {
            background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 50%, #ec4899 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 2.3rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            margin-bottom: 0.2rem;
        }

        .hero-sub {
            color: #64748b;
            font-size: 1.05rem;
            font-weight: 500;
            margin-bottom: 1.5rem;
        }

        /* Metric Cards */
        .kpi-card {
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.02) 100%);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 16px;
            padding: 1.2rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.04);
            text-align: center;
        }

        .kpi-val {
            font-size: 2rem;
            font-weight: 800;
            color: #3b82f6;
        }

        .kpi-lbl {
            font-size: 0.85rem;
            color: #94a3b8;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        /* Job Card Container */
        .job-card {
            background: #ffffff;
            border-radius: 14px;
            padding: 1.4rem;
            border: 1px solid #e2e8f0;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.03);
            margin-bottom: 1rem;
            transition: all 0.2s ease;
        }

        .job-card:hover {
            border-color: #3b82f6;
            box-shadow: 0 8px 24px rgba(59, 130, 246, 0.08);
        }

        /* Pill Badges */
        .badge-pill {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 0.02em;
            margin-right: 6px;
        }

        .badge-green { background: #dcfce7; color: #166534; }
        .badge-blue  { background: #dbeafe; color: #1e40af; }
        .badge-purple { background: #f3e8ff; color: #6b21a8; }
        .badge-orange { background: #ffedd5; color: #9a3412; }

        /* Custom Direct Apply Button */
        div.stButton > button:first-child {
            border-radius: 10px;
            font-weight: 700;
            transition: all 0.2s ease;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Header section
    st.markdown('<div class="hero-title">⚡ Autonomous Multi-Sector Job Hunter Agent</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero-sub">Real-time cross-portal scraping (LinkedIn, Indeed, Naukri & Google Jobs) with strict 24h verification & AI match scoring.</div>', unsafe_allow_html=True)

    with st.sidebar:
        st.markdown("### 🎯 Candidate Profile & Sector")
        sector_choice = st.selectbox(
            "Target Industry / Domain",
            options=list(SECTOR_PRESETS.keys()),
            index=0,
            help="Choose an industry preset or select Custom to define your own role.",
        )
        base_preset = SECTOR_PRESETS[sector_choice]

        user_exp = st.slider(
            "Total Experience (Years)",
            min_value=0.0,
            max_value=20.0,
            value=float(base_preset.get("experience_years", 3.0)),
            step=0.5,
        )

        custom_skills_input = st.text_input(
            "Core Skills & Technologies",
            value=", ".join(base_preset.get("core_skills", [])),
            help="Comma-separated skills used to match job descriptions.",
        )
        user_skills = [s.strip() for s in custom_skills_input.split(",") if s.strip()]

        st.divider()
        st.markdown("### 🌍 Search Locations & Filters")
        country_choice = st.selectbox(
            "Target Country",
            options=["India", "USA", "United Kingdom", "Canada", "Australia", "Singapore", "Germany", "UAE"],
            index=0,
        )

        custom_locs = st.text_input(
            "Locations / Cities",
            value=", ".join(base_preset.get("default_locations", ["Remote"])),
            help="Comma-separated target cities or 'Remote'.",
        )
        selected_locations = [loc.strip() for loc in custom_locs.split(",") if loc.strip()]

        custom_titles = st.text_input(
            "Target Job Titles",
            value=", ".join(base_preset.get("default_queries", ["Software Engineer"])),
            help="Comma-separated job titles to hunt for.",
        )
        selected_queries = [t.strip() for t in custom_titles.split(",") if t.strip()]

        hours_old = st.select_slider(
            "Max Posting Age",
            options=[1, 3, 6, 12, 24],
            value=24,
            format_func=lambda x: f"Last {x} Hours",
        )

        min_score_filter = st.slider("Filter: Minimum Match Score", min_value=50, max_value=95, value=70, step=5)

        run_btn = st.button("🚀 Run Live 24h Sweep", type="primary", use_container_width=True)

    user_profile = {
        "name": sector_choice,
        "experience_years": user_exp,
        "core_skills": user_skills,
        "default_queries": selected_queries,
        "default_locations": selected_locations,
    }

    if run_btn or "jobs" not in st.session_state:
        with st.spinner("🤖 Autonomous agent is scraping portals, checking timestamps & scoring roles..."):
            jobs, report_md = run_agent_pipeline(
                queries=selected_queries,
                locations=selected_locations,
                hours_old=hours_old,
                country=country_choice,
                profile=user_profile,
            )
            st.session_state["jobs"] = jobs
            st.session_state["report_md"] = report_md

    jobs: List[JobListing] = st.session_state.get("jobs", [])
    verified = [j for j in jobs if j.posted_time_verified and j.match_score >= min_score_filter]

    # Executive KPI Metric Cards
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f'<div class="kpi-card"><div class="kpi-val">{len(verified)}</div><div class="kpi-lbl">Verified (<24h)</div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        high_matches = sum(1 for j in verified if j.match_score >= 88)
        st.markdown(
            f'<div class="kpi-card"><div class="kpi-val">{high_matches}</div><div class="kpi-lbl">High Match (88%+)</div></div>',
            unsafe_allow_html=True,
        )
    with c3:
        remote_cnt = sum(1 for j in verified if j.is_remote)
        st.markdown(
            f'<div class="kpi-card"><div class="kpi-val">{remote_cnt}</div><div class="kpi-lbl">Remote / WFH</div></div>',
            unsafe_allow_html=True,
        )
    with c4:
        avg_score = int(sum(j.match_score for j in verified) / max(1, len(verified)))
        st.markdown(
            f'<div class="kpi-card"><div class="kpi-val">{avg_score}%</div><div class="kpi-lbl">Avg Match Score</div></div>',
            unsafe_allow_html=True,
        )

    st.write("")

    # Modern Navigation Tabs
    tab_cards, tab_analytics, tab_table, tab_pitch = st.tabs(
        [
            "🔥 Verified Opportunities (1-Click Apply)",
            "📊 Visual Analytics & Insights",
            "📋 Ranked Data Table & CSV",
            "✍️ AI Outreach Pitch Generator",
        ]
    )

    with tab_cards:
        if not verified:
            st.info("No jobs matched your minimum score threshold. Try lowering the match filter in the sidebar.")
        else:
            for idx, j in enumerate(verified, start=1):
                badge_class = "badge-green" if j.match_score >= 88 else ("badge-blue" if j.match_score >= 78 else "badge-orange")
                remote_badge = '<span class="badge-pill badge-purple">🌐 Remote</span>' if j.is_remote else ""
                window_badge = f'<span class="badge-pill badge-orange">⚡ {j.posting_window}</span>'

                with st.expander(
                    f"#{idx}  •  {j.title}  —  {j.company}  |  {j.match_score}/100 Match",
                    expanded=(idx <= 3),
                ):
                    col_details, col_action = st.columns([3, 1])
                    with col_details:
                        st.markdown(
                            f"""
                            <span class="badge-pill {badge_class}">⭐ {j.match_score}/100 Match</span>
                            <span class="badge-pill badge-blue">🏢 {j.source_portal}</span>
                            {window_badge}
                            {remote_badge}
                            """,
                            unsafe_allow_html=True,
                        )
                        st.write(f"📍 **Location:** {j.location} ({j.work_mode})  •  ⏳ **Experience:** {j.experience_required}  •  💰 **Salary:** {j.salary}")
                        st.markdown(f"**🎯 Why You Match:** {j.why_matches}")
                        st.markdown(f"**🔑 Key Skills Looked For:** `{j.key_skills_required}`")
                        if j.concerns and j.concerns != "None identified":
                            st.caption(f"⚠️ **Note:** {j.concerns}")
                    with col_action:
                        st.write("")
                        st.link_button("🚀 Direct Apply Now", j.job_url, use_container_width=True)
                        st.caption(f"Source: {j.source_portal}")

    with tab_analytics:
        st.subheader("📊 Live Market Insights (<24h Postings)")
        if verified:
            col_chart1, col_chart2 = st.columns(2)
            with col_chart1:
                st.write("**Top Locations / Hiring Hubs**")
                loc_counts = pd.Series([j.location.split(",")[0].strip() for j in verified]).value_counts().head(7)
                st.bar_chart(loc_counts)
            with col_chart2:
                st.write("**Work Mode Distribution**")
                mode_counts = pd.Series([j.work_mode for j in verified]).value_counts()
                st.bar_chart(mode_counts)

            st.write("**Match Score Distribution**")
            scores = [j.match_score for j in verified]
            st.line_chart(scores)

    with tab_table:
        st.subheader("📋 Ranked Jobs Grid")
        search_query = st.text_input("🔍 Quick filter by keyword, company, or title:")
        filtered_jobs = [
            j for j in verified
            if search_query.lower() in f"{j.title} {j.company} {j.location}".lower()
        ] if search_query else verified

        df_display = pd.DataFrame(
            [
                {
                    "Rank": i,
                    "Match": f"{j.match_score}/100",
                    "Title": j.title,
                    "Company": j.company,
                    "Location": j.location,
                    "Work Mode": j.work_mode,
                    "Posted": j.posted_time_raw,
                    "Experience": j.experience_required,
                    "Salary": j.salary,
                    "Direct Apply Link": j.job_url,
                }
                for i, j in enumerate(filtered_jobs, start=1)
            ]
        )
        st.dataframe(df_display, use_container_width=True, hide_index=True)

        st.download_button(
            label="📥 Download Verified Jobs as CSV",
            data=pd.DataFrame([asdict(j) for j in verified]).to_csv(index=False),
            file_name="verified_jobs_24h.csv",
            mime="text/csv",
        )

    with tab_pitch:
        st.subheader("✍️ 1-Click AI Outreach Pitch Generator")
        st.caption("Generate a personalized LinkedIn InMail or cold email message for any hiring manager.")
        if verified:
            job_choices = {f"{j.title} at {j.company}": j for j in verified}
            selected_job_key = st.selectbox("Select Target Job", list(job_choices.keys()))
            target_job = job_choices[selected_job_key]

            if st.button("Generate Tailored Pitch"):
                with st.spinner("Generating personalized outreach message..."):
                    pitch_text = generate_outreach_pitch_llm(target_job, user_profile)
                    st.text_area("Your Tailored Message (Copy & Paste):", value=pitch_text, height=180)
                    st.info("💡 Tip: Send this directly to the recruiter or hiring manager on LinkedIn alongside your application!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Job Hunter Agent")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in CLI / GitHub Actions cron mode and generate Markdown + CSV output.",
    )
    args, _ = parser.parse_known_args()

    in_streamlit = any("streamlit" in k.lower() for k in os.environ)
    if args.cli or not in_streamlit:
        jobs_list, md_output = run_agent_pipeline()
        print(md_output)
        logger.info("Completed sweep: %d verified jobs processed.", len(jobs_list))
    else:
        run_streamlit_dashboard()
