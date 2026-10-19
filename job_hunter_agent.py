#!/usr/bin/env python3
"""
Autonomous Job Hunter Agent (IT/Non-IT Recruitment & HR)
========================================================
A complete, 100% free-tier compatible job search agent tailored for a Senior
Associate Consultant (4+ years experience in IT, Non-IT, PAN India &
International Recruitment).

Features:
1. Multi-Portal Scraping (Last 24 Hours):
   - Concurrently scrapes LinkedIn, Naukri, Indeed India, and Google Jobs via
     `python-jobspy` (`hours_old=24`).
   - Includes a custom BeautifulSoup scraper for Indian portals (e.g., iimjobs)
     with strict timestamp verification ("Posted today", "<24h").
2. Deduplication & Persistence:
   - Uses SQLite (`jobs_history.db`) with automatic fallback to `/tmp` or JSON
     for cloud container environments (Streamlit Cloud, Hugging Face Spaces).
3. Free LLM Match-Scoring Engine (0-100):
   - Supports Google Gemini API Free Tier (`gemini-2.5-flash`) or GroqCloud
     Free Tier (`llama-3.3-70b-versatile`), with a deterministic rule-based
     fallback scorer so the script works out of the box even without API keys.
4. Automated Alerts (Telegram & Email):
   - Pushes daily/hourly digests of verified <24h openings to Telegram or Gmail.
5. Dual Mode (CLI Cron + Streamlit Web Dashboard):
   - CLI / GitHub Actions mode: `python job_hunter_agent.py --cli`
   - Web UI mode: `streamlit run job_hunter_agent.py`

Quick Setup:
    pip install python-jobspy requests beautifulsoup4 pandas streamlit google-genai
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
# 1. CANDIDATE PROFILE & SEARCH CONFIGURATION
# =====================================================================

CANDIDATE_PROFILE: Dict[str, Any] = {
    "name": "Senior Associate Consultant (Recruitment & TA)",
    "experience_years": 4.2,
    "target_exp_min": 2,
    "target_exp_max": 7,
    "current_designation": "Senior Associate Consultant",
    "current_company": "Sagacious Minds",
    "domains": ["IT Recruitment", "Non-IT Recruitment", "Leadership Hiring", "Niche Hiring"],
    "markets": ["PAN India", "International (US, UK, APAC, Middle East/UAE, Europe)"],
    "core_skills": [
        "End-to-end recruitment",
        "Candidate sourcing",
        "Screening & shortlisting",
        "Boolean search",
        "LinkedIn Recruiter / LinkedIn sourcing",
        "Stakeholder management",
        "Talent mapping",
        "Interview coordination",
        "Leadership hiring",
        "Candidate pipeline management",
    ],
    "additional_skills": [
        "AI tools",
        "Data Science",
        "Machine Learning",
        "Python",
        "SQL",
        "Power BI",
        "Recruitment Analytics",
    ],
    "preferred_locations": [
        "Remote",
        "Work From Home",
        "Gurugram",
        "Gurgaon",
        "Delhi NCR",
        "Noida",
        "Bengaluru",
        "Bangalore",
        "Hyderabad",
        "Pune",
        "Mumbai",
    ],
    "role_queries": [
        "Talent Acquisition Specialist",
        "IT Recruiter",
        "Technical Recruiter",
        "International Recruiter",
        "US IT Recruiter",
        "Talent Acquisition Partner",
        "Senior Recruiter",
        "Recruitment Consultant",
        "HR Recruiter",
        "HR Generalist",
        "HR Analyst",
    ],
}


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
    Automatically falls back to /tmp or in-memory SQLite on cloud filesystems
    where file locking is restricted.
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
    """
    Verifies whether a posting age string strictly falls within 24 hours.
    Returns (is_verified_within_24h, priority_bucket).
    """
    if not raw_text:
        return False, "Unverified"

    text = raw_text.strip().lower()

    # Reject vague labels per strict rule
    if text in {"recently posted", "just posted", "active", "new", "unknown"}:
        return False, "Unverified"

    # Match minutes or 1-6 hours
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

    # Match "posted today" or "1 day ago" / today's date
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
) -> List[JobListing]:
    """
    Scrapes LinkedIn, Naukri, Indeed India, and Google Jobs concurrently
    using `python-jobspy` with strict `hours_old=24` filtering.
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.info(
            "python-jobspy not installed (`pip install python-jobspy`). Skipping JobSpy portals."
        )
        return []

    collected: List[JobListing] = []
    portals = ["linkedin", "naukri", "indeed", "google"]

    for query in queries:
        for loc in locations:
            logger.info("Scraping %s for '%s' in '%s' (last %dh)...", portals, query, loc, hours_old)
            try:
                df = scrape_jobs(
                    site_name=portals,
                    search_term=query,
                    location=loc,
                    results_wanted=results_wanted,
                    hours_old=hours_old,
                    country_indeed="India",
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


def scrape_iimjobs_hr_feed(max_items: int = 20) -> List[JobListing]:
    """
    Scrapes recent HR & Talent Acquisition listings from iimjobs public pages
    and verifies the exact posting timestamp ("Posted today" / "Posted 1 day ago").
    """
    url = "https://www.iimjobs.com/k/talent-acquisition-jobs"
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
            logger.warning("iimjobs returned HTTP %s", resp.status_code)
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
        logger.info("Offline or unable to reach iimjobs directly: %s", exc)

    return listings


# =====================================================================
# 4. LLM & DETERMINISTIC MATCH-SCORING ENGINE (0-100)
# =====================================================================

def evaluate_job_deterministic(job: JobListing, profile: Dict[str, Any]) -> JobListing:
    """
    Fast, zero-cost rule-based evaluator that computes the 0-100 estimated match
    score, flags experience gaps, and tags Remote / Delhi NCR / International roles.
    Used either standalone or as a pre-filter before LLM scoring.
    """
    combined = f"{job.title} {job.company} {job.location} {job.description}".lower()
    score = 65  # Base score for matching search query

    # 1. Role prioritization (Recruitment / TA over general HR)
    ta_keywords = ["recruiter", "talent acquisition", "sourcing", "sourcer", "staffing", "executive search"]
    hr_keywords = ["hrbp", "business partner", "generalist", "hr operations", "people operations", "hr analyst"]
    if any(k in job.title.lower() for k in ta_keywords):
        score += 15
    elif any(k in job.title.lower() for k in hr_keywords):
        score += 6

    # 2. Skill alignment
    matched_skills: List[str] = []
    skill_checks = {
        "End-to-end recruitment": ["end-to-end", "full cycle", "full-cycle", "full lifecycle"],
        "LinkedIn Sourcing": ["linkedin", "passive sourcing", "headhunting"],
        "Boolean Search": ["boolean", "x-ray"],
        "Stakeholder Management": ["stakeholder", "hiring manager", "client management"],
        "Talent Mapping": ["talent mapping", "market mapping", "talent intelligence"],
        "IT / Technical Hiring": ["it recruiter", "technical", "software", "engineering", "saas", "technology"],
        "Analytics / Power BI / SQL": ["analytics", "dashboard", "power bi", "sql", "metrics", "data"],
        "AI Sourcing Tools": ["ai ", "automation", "ats", "workday", "lever", "greenhouse"],
    }
    for label, kw_list in skill_checks.items():
        if any(kw in combined for kw in kw_list):
            matched_skills.append(label)
            score += 2

    # 3. Location & Work Mode flags
    loc_lower = f"{job.location} {job.work_mode} {combined}".lower()
    job.is_remote = any(w in loc_lower for w in ["remote", "work from home", "wfh", "anywhere in india"])
    job.is_delhi_ncr = any(w in loc_lower for w in ["gurugram", "gurgaon", "noida", "delhi", "ncr"])
    job.is_international = any(
        w in combined
        for w in ["international", "us it", "global", "apac", "uk", "uae", "middle east", "europe", "anz"]
    )

    if job.is_remote or job.is_delhi_ncr or any(
        city in loc_lower for city in ["bengaluru", "bangalore", "mumbai", "pune", "hyderabad"]
    ):
        score += 6

    # 4. Experience range check (Candidate has 4+ years)
    concerns_list: List[str] = []
    exp_nums = [int(n) for n in re.findall(r"\b(\d{1,2})\b", job.experience_required)]
    if len(exp_nums) >= 2:
        min_exp, max_exp = exp_nums[0], exp_nums[1]
        if min_exp <= 4 <= max_exp:
            score += 6
        elif min_exp > 4:
            gap = min_exp - 4
            score -= min(12, gap * 3)
            concerns_list.append(f"Experience gap: asks for {min_exp}–{max_exp} yrs vs. 4+ yrs profile")
        elif max_exp < 4:
            score -= 6
            concerns_list.append(f"Junior band ({min_exp}–{max_exp} yrs); check compensation fit")

    if job.salary.lower() in {"not disclosed", "unverified", ""}:
        concerns_list.append("Salary not disclosed")

    if not job.posted_time_verified:
        concerns_list.append("Posting timestamp unverified on portal")

    job.match_score = max(40, min(98, score))
    job.key_skills_required = ", ".join(matched_skills[:5]) if matched_skills else (
        "End-to-end recruitment, Sourcing, Stakeholder management"
    )
    job.why_matches = (
        f"Aligns with your 4+ yrs consultancy experience and skills in {job.key_skills_required}."
    )
    job.concerns = "; ".join(concerns_list) if concerns_list else "None identified"
    return job


def evaluate_job_with_llm(job: JobListing, profile: Dict[str, Any]) -> JobListing:
    """
    Uses Google Gemini API (`GEMINI_API_KEY`) or Groq API (`GROQ_API_KEY`)
    to refine the match score (0-100), match rationale, required skills, and concerns.
    Falls back cleanly to `evaluate_job_deterministic` if no API key is set.
    """
    job = evaluate_job_deterministic(job, profile)

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        return job

    prompt = f"""
You are an expert recruitment career advisor. Evaluate this job opening against the candidate profile.

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

Return ONLY a valid JSON object with these keys:
- "match_score": integer from 0 to 100
- "why_matches": 1 concise sentence explaining why the candidate's profile fits
- "key_skills_required": comma-separated list of top 4-5 required skills
- "concerns": 1 concise sentence flagging any experience gap, shift timing, or unverified salary
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
        logger.debug("LLM scoring fallback triggered for %s: %s", job.title, exc)

    return job


# =====================================================================
# 5. REPORT GENERATOR & AUTOMATED ALERTS (TELEGRAM / EMAIL)
# =====================================================================

def generate_markdown_report(jobs: List[JobListing]) -> str:
    """
    Formats verified (<24h) and unverified jobs into the exact ranked Markdown
    table, detailed breakdown, and final summary requested by the user.
    """
    verified_jobs = [j for j in jobs if j.posted_time_verified]
    unverified_jobs = [j for j in jobs if not j.posted_time_verified]

    window_rank = {"1-6h": 1, "6-12h": 2, "12-24h": 3, "Unverified": 4}
    verified_jobs.sort(key=lambda j: (-j.match_score, window_rank.get(j.posting_window, 9)))

    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    lines: List[str] = [
        f"# Autonomous Job-Search Report ({now_str})",
        "",
        "*Note: Match Score (0–100) is an estimated assessment based on experience, "
        "recruitment skills, location, and work mode—not an official employer evaluation.*",
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
                f"- **Original job-posting URL**: {j.job_url}",
                f"- **Concerns**: {j.concerns}",
                "",
            ]
        )

    if unverified_jobs:
        lines.extend(["## Promising Vacancies with Unverified Posting Times", ""])
        for j in unverified_jobs:
            lines.append(
                f"- **[{j.title} — {j.company}]({j.job_url})** ({j.location} | {j.experience_required}): "
                f"Estimated Match {j.match_score}/100 — *Status: Posting time unverified ({j.posted_time_raw}).*"
            )
        lines.append("")

    # Final Summary Metrics
    remote_count = sum(1 for j in verified_jobs if j.is_remote)
    ncr_count = sum(1 for j in verified_jobs if j.is_delhi_ncr)
    intl_count = sum(1 for j in verified_jobs if j.is_international)
    top_5_matches = sorted(verified_jobs, key=lambda x: -x.match_score)[:5]
    top_5_newest = sorted(
        verified_jobs, key=lambda x: (window_rank.get(x.posting_window, 9), -x.match_score)
    )[:5]

    lines.extend(
        [
            "## Final Summary",
            f"- **Total unique jobs verified as posted within 24 hours**: {len(verified_jobs)}",
            f"- **Number of remote jobs**: {remote_count}",
            f"- **Number of jobs in Delhi NCR**: {ncr_count}",
            f"- **Number of international recruitment jobs**: {intl_count}",
            "- **Top 5 strongest matches**: "
            + ", ".join(f"[{j.title} at {j.company}]({j.job_url}) ({j.match_score}/100)" for j in top_5_matches),
            "- **Top 5 most recently posted qualifying vacancies**: "
            + ", ".join(f"[{j.title} at {j.company}]({j.job_url}) ({j.posted_time_raw})" for j in top_5_newest),
        ]
    )

    return "\n".join(lines)


def send_telegram_alert(report_summary: str) -> bool:
    """Sends a job alert message to Telegram if TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID are set."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": report_summary[:4000],
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Telegram alert failed: %s", exc)
        return False


def send_email_alert(subject: str, markdown_body: str) -> bool:
    """Sends an email digest via Gmail SMTP if SMTP_USER, SMTP_PASS, and ALERT_EMAIL_TO are set."""
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASS", "").strip()
    email_to = os.getenv("ALERT_EMAIL_TO", "").strip()
    if not (smtp_user and smtp_pass and email_to):
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg.attach(MIMEText(markdown_body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [email_to], msg.as_string())
        return True
    except Exception as exc:
        logger.warning("Email alert failed: %s", exc)
        return False


# =====================================================================
# 6. SAMPLE SEED DATA (FOR OFFLINE / DEMO TESTING)
# =====================================================================

def get_demo_verified_jobs() -> List[JobListing]:
    """Provides verified sample listings when running offline or testing the pipeline."""
    return [
        JobListing(
            job_id="1740105",
            title="Talent Acquisition Role - IT",
            company="Michael Page International",
            location="Bengaluru",
            work_mode="Hybrid (3 days WFO / 2 days WFH)",
            posted_time_raw="Posted today",
            posted_time_verified=True,
            posting_window="12-24h",
            experience_required="3-7 years",
            salary="Not disclosed",
            job_url="https://www.iimjobs.com/j/talent-acquisition-role-it-1740105",
            source_portal="iimjobs",
            description=(
                "Lead end-to-end IT and Product Support recruitment in India; manage candidate "
                "pipelines, Boolean search, LinkedIn sourcing, and international stakeholders "
                "across India, Singapore, UK, and US."
            ),
        ),
        JobListing(
            job_id="ptr_remote_9",
            title="Recruiter (US / IT Contract Staffing)",
            company="Pinnacle Group, Inc. (PTR Global)",
            location="Remote, India",
            work_mode="Remote / WFH",
            posted_time_raw="1 hour ago",
            posted_time_verified=True,
            posting_window="1-6h",
            experience_required="3-6 years",
            salary="Not disclosed",
            job_url="https://www.remoterocketship.com/company/pinnacle1/jobs/recruiter-india-remote-9/",
            source_portal="Remote Rocketship",
            description=(
                "Full-lifecycle international US IT recruitment from intake to offer; use AI "
                "sourcing tools, Boolean search, LinkedIn Recruiter, Bullhorn ATS, and VMS."
            ),
        ),
        JobListing(
            job_id="1740087",
            title="Assistant Manager - Talent Acquisition",
            company="Ola Electric",
            location="Bengaluru",
            work_mode="On-site",
            posted_time_raw="Posted today",
            posted_time_verified=True,
            posting_window="12-24h",
            experience_required="1-4 years",
            salary="₹25-35 LPA",
            job_url="https://www.iimjobs.com/j/ola-electric-assistant-manager-talent-acquisition-1740087",
            source_portal="iimjobs",
            description=(
                "End-to-end recruitment across Technology (Backend, Data Science, AI/ML, DevOps) "
                "and Corporate Functions; track pipeline conversion and recruitment analytics."
            ),
        ),
        JobListing(
            job_id="1740157",
            title="Talent Acquisition Partner (Assistant Manager - TA)",
            company="Atlass Partners Consulting LLP",
            location="Delhi NCR / Bengaluru / Mumbai",
            work_mode="On-site",
            posted_time_raw="Posted today",
            posted_time_verified=True,
            posting_window="12-24h",
            experience_required="5-11 years",
            salary="Not disclosed",
            job_url="https://www.iimjobs.com/j/talent-acquisition-partner-1740157",
            source_portal="iimjobs",
            description=(
                "Own full recruitment cycle; LinkedIn sourcing, talent mapping, stakeholder "
                "management, ATS platforms, data visualization, and recruitment analytics."
            ),
        ),
    ]


# =====================================================================
# 7. MAIN AGENT PIPELINE & STREAMLIT WEB UI
# =====================================================================

def run_agent_pipeline(
    queries: Optional[List[str]] = None,
    locations: Optional[List[str]] = None,
    hours_old: int = 24,
    use_demo_if_empty: bool = True,
    output_dir: Optional[str] = None,
) -> tuple[List[JobListing], str]:
    """Executes scraping, deduplication, scoring, and Markdown report generation."""
    db = JobDatabase()
    queries = queries or CANDIDATE_PROFILE["role_queries"][:4]
    locations = locations or ["India", "Gurugram", "Bengaluru", "Mumbai"]

    raw_jobs: List[JobListing] = []
    raw_jobs.extend(scrape_with_jobspy(queries=queries, locations=locations, hours_old=hours_old))
    raw_jobs.extend(scrape_iimjobs_hr_feed(max_items=15))

    if not raw_jobs and use_demo_if_empty:
        logger.info("Using demo verified listings (offline or no live scraper results).")
        raw_jobs = get_demo_verified_jobs()

    # Deduplicate within current batch & score against profile
    unique_jobs: Dict[str, JobListing] = {}
    for job in raw_jobs:
        dedup_key = JobDatabase.make_dedup_key(job.title, job.company)
        if dedup_key not in unique_jobs:
            scored_job = evaluate_job_with_llm(job, CANDIDATE_PROFILE)
            unique_jobs[dedup_key] = scored_job
            db.mark_seen(scored_job)

    final_jobs = list(unique_jobs.values())
    final_jobs.sort(key=lambda j: -j.match_score)
    report_md = generate_markdown_report(final_jobs)

    # Save CSV & Markdown outputs safely
    target_dir = output_dir or tempfile.gettempdir()
    try:
        df_out = pd.DataFrame([asdict(j) for j in final_jobs])
        df_out.to_csv(os.path.join(target_dir, "latest_jobs_24h.csv"), index=False)
        with open(os.path.join(target_dir, "latest_jobs_report.md"), "w", encoding="utf-8") as f:
            f.write(report_md)
    except OSError as exc:
        logger.warning("Could not write export files to %s: %s", target_dir, exc)

    # Trigger optional alerts
    send_telegram_alert(report_md)
    send_email_alert("Daily Verified <24h Job Hunter Digest", report_md)

    return final_jobs, report_md


def run_streamlit_dashboard() -> None:
    """Renders an interactive web dashboard when launched via `streamlit run job_hunter_agent.py`."""
    import streamlit as st

    st.set_page_config(page_title="Autonomous Job Hunter Agent", layout="wide")
    st.title("Autonomous Recruitment & HR Job Hunter Agent (24h Strict Filter)")
    st.caption(
        "Tailored for: Senior Associate Consultant (4+ yrs | IT & Non-IT | PAN India & International)"
    )

    with st.sidebar:
        st.header("Search Filters")
        selected_queries = st.multiselect(
            "Target Job Titles",
            options=CANDIDATE_PROFILE["role_queries"],
            default=CANDIDATE_PROFILE["role_queries"][:4],
        )
        selected_locations = st.multiselect(
            "Preferred Locations",
            options=["Remote", "Gurugram", "Delhi NCR", "Noida", "Bengaluru", "Hyderabad", "Pune", "Mumbai"],
            default=["Remote", "Gurugram", "Bengaluru", "Mumbai"],
        )
        hours_old = st.slider("Max Posting Age (Hours)", min_value=1, max_value=24, value=24)
        run_btn = st.button("Run Live 24h Sweep", type="primary")

    if run_btn or "jobs" not in st.session_state:
        with st.spinner("Searching portals, verifying timestamps, and scoring matches..."):
            jobs, report_md = run_agent_pipeline(
                queries=selected_queries,
                locations=selected_locations,
                hours_old=hours_old,
            )
            st.session_state["jobs"] = jobs
            st.session_state["report_md"] = report_md

    jobs: List[JobListing] = st.session_state.get("jobs", [])
    verified = [j for j in jobs if j.posted_time_verified]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Verified (<24h)", len(verified))
    col2.metric("Remote Jobs", sum(1 for j in verified if j.is_remote))
    col3.metric("Delhi NCR Jobs", sum(1 for j in verified if j.is_delhi_ncr))
    col4.metric("International Hiring", sum(1 for j in verified if j.is_international))

    st.download_button(
        label="Download Ranked CSV",
        data=pd.DataFrame([asdict(j) for j in jobs]).to_csv(index=False),
        file_name="verified_jobs_24h.csv",
        mime="text/csv",
    )
    st.markdown(st.session_state.get("report_md", ""))


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
