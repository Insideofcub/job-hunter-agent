#!/usr/bin/env python3
"""
Autonomous Multi-Sector Job Hunter Agent (Premium Executive Edition)
===================================================================
A 100% free-tier compatible job search agent supporting any sector (Recruitment,
IT/Tech, Sales, Retail, Finance, Marketing), worldwide regions, all countries/cities,
real-time Start/Stop controls, customizable posting age, optional CV/Resume
matching engine, and instant Telegram Bot notification alerts.

Key Capabilities:
1. Global Coverage (All Regions, Countries & Cities):
   - Presets for North America, Europe & UK, Asia Pacific & South Asia, Middle East & Africa,
     Latin America, Global Remote, and Worldwide All Regions.
   - Fully customizable text inputs for any city, country, or location worldwide.
2. Interactive Agent Controls (Start & Stop Buttons):
   - Real-time "▶️ Run Agent" and "⏹️ Stop Agent" controls with live status pill
     (🟢 Hunting Jobs / ⚪ Ready / 🛑 Stopped) and interrupt callbacks.
3. Flexible Posting Age Filter:
   - 24 Hours, 2 Days (48h), 7 Days (168h), 15 Days (360h), 30 Days (720h), or Custom Days.
4. Optional CV / Resume Matcher:
   - Upload resume (PDF, DOCX, TXT) or paste text.
   - Computes Resume Match Score (0–100%), identifies matching skills, reveals missing JD keywords,
     and provides ATS tailoring recommendations. Completely optional.
5. Telegram Alert Settings & Notification Engine:
   - Real-time Telegram Bot integration (`send_telegram_message`).
   - Configure Bot Token & Chat ID in sidebar or dedicated Telegram tab.
   - "🧪 Test Bot" and "📢 Push Jobs to Telegram" triggers with live feedback.
   - 1-Click "📱 Send to Telegram" button on individual job cards.
   - Automatic dispatch in CLI & GitHub Actions cron sweeps.
6. Full Job Description (JD) Extraction:
   - Extracts complete JDs and provides an in-app scrollable reader with 1-click Direct Apply links.
7. Hybrid Match Scoring:
   - Gemini 2.5 Flash API free tier or zero-cost rule-based heuristic fallback.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html
import json
import logging
import os
import re
import smtplib
import sqlite3
import tempfile
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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
# 1. GLOBAL REGIONS, COUNTRIES, SECTOR PRESETS & CANDIDATE TAXONOMY
# =====================================================================

GLOBAL_REGIONS: Dict[str, Dict[str, Any]] = {
    "Worldwide / All Regions": {
        "countries": [
            "All Countries / Global",
            "United States",
            "India",
            "United Kingdom",
            "Germany",
            "Canada",
            "Australia",
            "Singapore",
            "United Arab Emirates",
            "Netherlands",
            "Custom / Other...",
        ],
        "default_country": "All Countries / Global",
        "default_cities": ["Remote", "New York", "London", "Bengaluru", "Berlin", "Toronto", "Sydney", "Singapore", "Dubai"],
    },
    "Global Remote / Anywhere": {
        "countries": ["Worldwide / Remote", "United States", "United Kingdom", "Canada", "Germany", "India"],
        "default_country": "Worldwide / Remote",
        "default_cities": ["Remote", "Worldwide Remote", "Work From Anywhere", "Anywhere"],
    },
    "North America": {
        "countries": ["United States", "Canada", "Mexico", "Custom / Other..."],
        "default_country": "United States",
        "default_cities": ["Remote", "New York, NY", "San Francisco, CA", "Seattle, WA", "Austin, TX", "Chicago, IL", "Boston, MA", "Los Angeles, CA", "Toronto, ON", "Vancouver, BC"],
    },
    "Europe & United Kingdom": {
        "countries": [
            "United Kingdom",
            "Germany",
            "Netherlands",
            "France",
            "Ireland",
            "Switzerland",
            "Sweden",
            "Spain",
            "Italy",
            "Poland",
            "Custom / Other...",
        ],
        "default_country": "United Kingdom",
        "default_cities": ["Remote", "London", "Berlin", "Amsterdam", "Dublin", "Paris", "Munich", "Zurich", "Stockholm", "Barcelona", "Warsaw"],
    },
    "Asia Pacific & South Asia": {
        "countries": [
            "India",
            "Singapore",
            "Australia",
            "Japan",
            "New Zealand",
            "Malaysia",
            "Philippines",
            "Hong Kong",
            "South Korea",
            "Custom / Other...",
        ],
        "default_country": "India",
        "default_cities": ["Remote", "Bengaluru", "Gurugram", "Mumbai", "Hyderabad", "Pune", "Delhi NCR", "Singapore", "Sydney", "Melbourne", "Tokyo"],
    },
    "Middle East & Africa": {
        "countries": ["United Arab Emirates", "Saudi Arabia", "South Africa", "Qatar", "Israel", "Egypt", "Custom / Other..."],
        "default_country": "United Arab Emirates",
        "default_cities": ["Remote", "Dubai", "Abu Dhabi", "Riyadh", "Doha", "Johannesburg", "Cape Town", "Tel Aviv", "Cairo"],
    },
    "Latin America": {
        "countries": ["Brazil", "Argentina", "Colombia", "Chile", "Mexico", "Peru", "Custom / Other..."],
        "default_country": "Brazil",
        "default_cities": ["Remote", "São Paulo", "Buenos Aires", "Bogotá", "Santiago", "Mexico City", "Lima"],
    },
}

COUNTRY_CODE_MAP: Dict[str, str] = {
    "all countries / global": "USA",
    "worldwide / remote": "USA",
    "united states": "USA",
    "usa": "USA",
    "us": "USA",
    "india": "India",
    "united kingdom": "UK",
    "uk": "UK",
    "canada": "Canada",
    "australia": "Australia",
    "germany": "Germany",
    "singapore": "Singapore",
    "united arab emirates": "UAE",
    "uae": "UAE",
    "netherlands": "Netherlands",
    "france": "France",
    "ireland": "Ireland",
    "switzerland": "Switzerland",
    "sweden": "Sweden",
    "spain": "Spain",
    "italy": "Italy",
    "poland": "Poland",
    "japan": "Japan",
    "new zealand": "New Zealand",
    "brazil": "Brazil",
    "mexico": "Mexico",
    "saudi arabia": "Saudi Arabia",
    "south africa": "South Africa",
}

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
        "core_skills": ["Python", "JavaScript", "React", "Node.js", "SQL", "Docker", "AWS", "Git", "REST APIs"],
        "default_queries": [
            "Software Engineer",
            "Full Stack Developer",
            "Backend Developer",
            "Python Developer",
            "DevOps Engineer",
        ],
        "default_locations": ["Remote", "Bengaluru", "San Francisco, CA", "London", "Berlin"],
    },
    "B2B Sales & Business Development": {
        "name": "Business Development & Account Executive",
        "experience_years": 4.0,
        "domains": ["SaaS Sales", "B2B Tech", "Key Account Management"],
        "core_skills": ["Lead generation", "Pipeline management", "CRM", "Cold outreach", "Negotiation", "Salesforce"],
        "default_queries": [
            "Account Executive",
            "Business Development Manager",
            "Sales Manager",
            "Inside Sales Specialist",
        ],
        "default_locations": ["Remote", "New York, NY", "London", "Dubai", "Singapore"],
    },
    "Finance & Accounting": {
        "name": "Financial Analyst / Accounting Specialist",
        "experience_years": 3.0,
        "domains": ["Corporate Finance", "Financial Reporting", "Auditing"],
        "core_skills": ["Financial modeling", "Excel", "MIS reporting", "GAAP/IFRS", "SAP", "Forecasting"],
        "default_queries": [
            "Financial Analyst",
            "Senior Accountant",
            "Finance Manager",
            "Auditor",
        ],
        "default_locations": ["Remote", "London", "New York, NY", "Mumbai", "Singapore"],
    },
    "Marketing & Growth": {
        "name": "Growth Marketing Specialist",
        "experience_years": 3.5,
        "domains": ["Digital Marketing", "SEO/SEM", "Performance Marketing", "Content Strategy"],
        "core_skills": ["Google Analytics", "Performance Marketing", "SEO", "Content Marketing", "PPC", "Growth Hacking"],
        "default_queries": [
            "Growth Marketing Manager",
            "Performance Marketing Specialist",
            "Digital Marketing Manager",
            "SEO Specialist",
        ],
        "default_locations": ["Remote", "Austin, TX", "London", "Bengaluru", "Amsterdam"],
    },
    "Custom / Any Sector": {
        "name": "Custom Candidate",
        "experience_years": 3.0,
        "domains": ["General"],
        "core_skills": ["Communication", "Problem Solving", "Project Management", "Leadership"],
        "default_queries": ["Operations Manager", "Project Manager", "Product Manager"],
        "default_locations": ["Remote", "Worldwide"],
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
    posting_window: str  # e.g., "1-6h", "12-24h", "2d", "7d", "15d", "30d"
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
    # Optional CV match attributes
    cv_match_score: Optional[int] = None
    cv_matching_skills: str = ""
    cv_missing_skills: str = ""
    cv_tailoring_tips: str = ""


# =====================================================================
# 2. DEDUPLICATION & PERSISTENCE LAYER (SQLITE + CLOUD FALLBACK)
# =====================================================================

class JobDatabase:
    """
    Lightweight SQLite store to deduplicate jobs across portals and runs.
    Automatically falls back to /tmp or in-memory SQLite on cloud hosts.
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
# 3. TELEGRAM ALERT & NOTIFICATION ENGINE
# =====================================================================

def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
) -> Tuple[bool, str]:
    """
    Sends a formatted notification message to a Telegram chat or channel.
    Returns (success: bool, status_message: str).
    """
    clean_token = (bot_token or "").strip()
    clean_chat_id = (chat_id or "").strip()

    if not clean_token or not clean_chat_id:
        return False, "Telegram Bot Token and Chat ID are required."

    url = f"https://api.telegram.org/bot{clean_token}/sendMessage"
    payload = {
        "chat_id": clean_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=12)
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            return True, "Telegram alert sent successfully!"
        else:
            err_desc = data.get("description", f"HTTP {resp.status_code}")
            return False, f"Telegram API error: {err_desc}"
    except Exception as exc:
        return False, f"Network error sending Telegram alert: {exc}"


def format_jobs_for_telegram(jobs: List[JobListing], max_jobs: int = 5) -> str:
    """Formats top qualifying jobs into an executive Telegram digest."""
    top_jobs = [j for j in jobs if j.posted_time_verified][:max_jobs]
    if not top_jobs:
        top_jobs = jobs[:max_jobs]

    now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    lines = [
        "⚡ <b>AI Job Hunter Agent — Verified Opportunities</b>",
        f"🕒 <i>Swept on {now_str}</i>\n",
    ]
    for idx, j in enumerate(top_jobs, start=1):
        safe_title = html.escape(j.title)
        safe_company = html.escape(j.company)
        safe_loc = html.escape(j.location)
        cv_text = f" | 📄 CV Match: <b>{j.cv_match_score}%</b>" if j.cv_match_score is not None else ""
        lines.append(
            f"<b>{idx}. {safe_title}</b>\n"
            f"🏢 <b>{safe_company}</b>\n"
            f"📍 {safe_loc} ({j.work_mode})\n"
            f"⭐ Match: <b>{j.match_score}/100</b>{cv_text}\n"
            f"⏱️ {j.posted_time_raw} ({j.posting_window})\n"
            f"💰 {html.escape(j.salary)}\n"
            f"🔗 <a href='{j.job_url}'>Direct Apply Link</a>\n"
        )
    lines.append("🤖 <i>Automated sweep by Job Hunter Agent</i>")
    return "\n".join(lines)


def format_single_job_for_telegram(job: JobListing) -> str:
    """Formats a single job opportunity into a detailed Telegram alert card."""
    safe_title = html.escape(job.title)
    safe_company = html.escape(job.company)
    safe_loc = html.escape(job.location)
    cv_info = f"\n📄 <b>CV Match:</b> {job.cv_match_score}%" if job.cv_match_score is not None else ""
    return (
        f"🎯 <b>Job Alert: {safe_title}</b>\n"
        f"🏢 <b>Company:</b> {safe_company}\n"
        f"📍 <b>Location:</b> {safe_loc} ({job.work_mode})\n"
        f"⭐ <b>Match Score:</b> {job.match_score}/100{cv_info}\n"
        f"⏱️ <b>Posted:</b> {job.posted_time_raw} ({job.posting_window})\n"
        f"💰 <b>Salary:</b> {html.escape(job.salary)}\n"
        f"🔑 <b>Key Skills:</b> {html.escape(job.key_skills_required)}\n"
        f"🎯 <b>Why You Match:</b> {html.escape(job.why_matches)}\n\n"
        f"🚀 <a href='{job.job_url}'>Click Here to Apply Directly</a>"
    )


# =====================================================================
# 4. MULTI-PORTAL SCRAPERS (CUSTOMIZABLE AGE & STOP SIGNAL)
# =====================================================================

def classify_posting_age(raw_text: str, max_hours: int = 24) -> Tuple[bool, str]:
    """
    Verifies whether a posting age string falls within the user-specified max_hours.
    Returns (is_verified_within_window, priority_bucket).
    """
    if not raw_text:
        return False, "Unverified"

    text = raw_text.strip().lower()

    if text in {"recently posted", "just posted", "active", "new", "unknown"}:
        return False, "Unverified"

    # Match minutes
    min_match = re.search(r"(\d+)\s*(?:min|minute|minutes|m)\b", text)
    if min_match or "0 minutes ago" in text or "few minutes ago" in text:
        return True, "1-6h"

    # Match hours
    hr_match = re.search(r"(\d+)\s*(?:hr|hrs|hour|hours|h)\b", text)
    if hr_match:
        hrs = int(hr_match.group(1))
        if hrs <= max_hours:
            if hrs <= 6:
                return True, "1-6h"
            if hrs <= 12:
                return True, "6-12h"
            if hrs <= 24:
                return True, "12-24h"
            return True, f"{hrs}h ago"
        return False, f"Older than {max_hours}h"

    # Match days
    day_match = re.search(r"(\d+)\s*(?:day|days|d)\b", text)
    if day_match:
        days = int(day_match.group(1))
        if days * 24 <= max_hours:
            if days <= 1:
                return True, "12-24h"
            if days <= 2:
                return True, "2 days ago"
            if days <= 7:
                return True, f"{days} days ago"
            if days <= 15:
                return True, f"{days} days ago"
            return True, f"{days} days ago"
        return False, f"Older than {max_hours // 24} days"

    # Match "posted today" / "yesterday"
    if "posted today" in text or "today" in text:
        return True, "12-24h"
    if "1 day ago" in text or "yesterday" in text:
        if max_hours >= 24:
            return True, "12-24h"
        return False, "Older than requested hours"

    return False, "Unverified"


def scrape_with_jobspy(
    queries: List[str],
    locations: List[str],
    hours_old: int = 24,
    results_wanted: int = 15,
    country_indeed: str = "USA",
    stop_checker: Optional[Callable[[], bool]] = None,
) -> List[JobListing]:
    """
    Scrapes LinkedIn, Naukri, Indeed, and Google Jobs with early stop check
    and full Job Description extraction.
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.info("python-jobspy not installed. Skipping JobSpy portals.")
        return []

    collected: List[JobListing] = []
    portals = ["linkedin", "indeed", "google"]
    mapped_country = COUNTRY_CODE_MAP.get(country_indeed.lower().strip(), country_indeed)
    if mapped_country.lower() in {"india", "in"}:
        portals.append("naukri")

    for query in queries:
        if stop_checker and stop_checker():
            logger.info("Agent stopped early by user request.")
            break
        for loc in locations:
            if stop_checker and stop_checker():
                break

            logger.info(
                "Scraping %s for '%s' in '%s' (%s, max %dh)...",
                portals, query, loc, mapped_country, hours_old
            )
            try:
                df = scrape_jobs(
                    site_name=portals,
                    search_term=query,
                    location=loc,
                    results_wanted=results_wanted,
                    hours_old=hours_old,
                    country_indeed=mapped_country,
                )
            except Exception as exc:
                logger.warning("JobSpy query failed for %s (%s): %s", query, loc, exc)
                continue

            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                if stop_checker and stop_checker():
                    break

                title = str(row.get("title") or "").strip()
                company = str(row.get("company") or "Unknown Employer").strip()
                job_url = str(row.get("job_url") or "").strip()
                if not title or not job_url:
                    continue

                date_posted = str(row.get("date_posted") or "Posted today")
                verified, bucket = classify_posting_age(date_posted, max_hours=hours_old)
                loc_str = str(row.get("location") or loc).strip()
                is_remote_flag = bool(row.get("is_remote")) or "remote" in loc_str.lower()
                work_mode = "Remote / WFH" if is_remote_flag else "Hybrid / On-site"

                min_amt = row.get("min_amount")
                max_amt = row.get("max_amount")
                currency = row.get("currency") or "USD"
                if pd.notna(min_amt) and pd.notna(max_amt):
                    salary_str = f"{currency} {min_amt:,.0f} - {max_amt:,.0f}"
                else:
                    salary_str = "Not disclosed"

                full_description = str(row.get("description") or "").strip()
                exp_match = re.search(r"(\d+\s*[-–to]+\s*\d+\s*(?:years|yrs))", full_description, re.I)
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
                        description=full_description,
                    )
                )

    return collected


def scrape_iimjobs_feed(
    category: str = "talent-acquisition-jobs",
    max_items: int = 20,
    max_hours: int = 24,
    stop_checker: Optional[Callable[[], bool]] = None,
) -> List[JobListing]:
    """Scrapes recent openings from iimjobs with custom max_hours checking and stop check."""
    if stop_checker and stop_checker():
        return []

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
            if stop_checker and stop_checker():
                break

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
            verified, bucket = classify_posting_age(posted_raw, max_hours=max_hours)

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
# 5. OPTIONAL CV / RESUME PARSING & MATCHING ENGINE
# =====================================================================

def extract_text_from_upload(uploaded_file) -> str:
    """Extracts plain text from an uploaded PDF, DOCX, or TXT file."""
    if uploaded_file is None:
        return ""
    file_name = uploaded_file.name.lower()
    try:
        if file_name.endswith(".pdf"):
            import pypdf

            reader = pypdf.PdfReader(uploaded_file)
            text_parts = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(text_parts).strip()
        elif file_name.endswith(".docx"):
            import docx

            doc = docx.Document(uploaded_file)
            return "\n".join([p.text for p in doc.paragraphs if p.text]).strip()
        else:
            return uploaded_file.read().decode("utf-8", errors="ignore").strip()
    except Exception as exc:
        logger.warning("Error parsing uploaded resume: %s", exc)
        return ""


def extract_keywords_from_text(text: str) -> Set[str]:
    """Extracts normalized key terms, technologies, and skills from text."""
    stopwords = {
        "and", "the", "with", "for", "from", "that", "this", "will", "have", "been",
        "work", "team", "role", "looking", "candidate", "experience", "skills",
        "years", "strong", "ability", "across", "about", "other", "their", "such",
        "responsibilities", "requirements", "preferred", "must", "plus", "including",
        "working", "within", "daily", "join", "help", "drive", "build", "create",
    }
    raw_tokens = re.findall(r"\b[a-zA-Z0-9\+\#\.\-]{2,20}\b", text.lower())
    return {t for t in raw_tokens if t not in stopwords and not t.isdigit() and len(t) >= 3}


def calculate_resume_match(
    resume_text: str,
    job: JobListing,
    gemini_key: str = "",
) -> Dict[str, Any]:
    """
    Compares candidate's resume text against a specific job's title, description,
    and requirements. Returns score (0-100), matching skills, missing keywords,
    and tailoring suggestions.
    """
    if not resume_text or len(resume_text.strip()) < 30:
        return {}

    resume_tokens = extract_keywords_from_text(resume_text)
    jd_tokens = extract_keywords_from_text(f"{job.title} {job.description} {job.key_skills_required}")

    if not jd_tokens:
        return {"score": 75, "matching": [], "missing": [], "tips": "Review job portal requirements."}

    overlap = resume_tokens.intersection(jd_tokens)
    missing = jd_tokens.difference(resume_tokens)

    coverage_ratio = len(overlap) / max(1, len(jd_tokens))
    base_score = int(min(96, max(45, (coverage_ratio * 120) + 40)))

    title_tokens = extract_keywords_from_text(job.title)
    if title_tokens.intersection(resume_tokens):
        base_score = min(98, base_score + 10)

    top_matching = sorted(list(overlap), key=lambda w: -len(w))[:8]
    top_missing = sorted(list(missing), key=lambda w: -len(w))[:6]

    result = {
        "score": base_score,
        "matching": top_matching,
        "missing": top_missing,
        "tips": (
            f"Add mentions of '{', '.join(top_missing[:3])}' into your project highlights or summary "
            f"to boost ATS score for {job.company}."
            if top_missing else "Strong keyword alignment with the job description!"
        ),
    }

    if gemini_key:
        prompt = f"""
Compare this candidate's Resume against the Target Job Description.
Return ONLY valid JSON with keys:
- "score": integer 0-100 representing resume-to-job match
- "matching": list of top 5-6 matching skills/strengths
- "missing": list of top 4-5 missing skills or keywords the candidate should add
- "tips": 1-2 actionable sentences on how to tailor the resume for this exact role

RESUME (Excerpt):
{resume_text[:2000]}

TARGET JOB:
Title: {job.title} at {job.company}
Description: {job.description[:1800]}
"""
        try:
            from google import genai

            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            raw = resp.text.strip().removeprefix("```json").removesuffix("```").strip()
            parsed = json.loads(raw)
            result["score"] = int(parsed.get("score", result["score"]))
            result["matching"] = parsed.get("matching", result["matching"])
            result["missing"] = parsed.get("missing", result["missing"])
            result["tips"] = str(parsed.get("tips", result["tips"]))
        except Exception as exc:
            logger.debug("Gemini resume match fallback: %s", exc)

    return result


# =====================================================================
# 6. GENERALIZED MATCH-SCORING ENGINE (LLM + RULE-BASED FALLBACK)
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

    job.is_remote = any(w in loc_lower for w in ["remote", "work from home", "wfh", "anywhere"])
    job.is_delhi_ncr = any(w in loc_lower for w in ["gurugram", "gurgaon", "noida", "delhi", "ncr"])
    job.is_international = any(
        w in combined for w in ["international", "us ", "uk ", "apac", "uae", "middle east", "europe", "worldwide"]
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
Description: {job.description[:1800]}

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
            f"Hi Hiring Team,\n\nI noticed the {job.title} role at {job.company} posted recently on {job.source_portal} "
            f"and wanted to connect. With {profile.get('experience_years', 4)}+ years of experience in "
            f"{', '.join(profile.get('core_skills', [])[:4])}, my background directly aligns with your requirements.\n\n"
            f"I would welcome the opportunity to discuss how I can contribute to {job.company}'s goals.\n\n"
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
# 7. REPORT GENERATOR & EXPORT
# =====================================================================

def generate_markdown_report(
    jobs: List[JobListing],
    profile_title: str = "Candidate",
    hours_window: int = 24,
) -> str:
    """Formats verified jobs into a ranked Markdown table and summary with direct links."""
    verified_jobs = [j for j in jobs if j.posted_time_verified]
    unverified_jobs = [j for j in jobs if not j.posted_time_verified]

    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    window_label = f"Last {hours_window // 24} Days" if hours_window > 24 else f"Last {hours_window} Hours"

    lines: List[str] = [
        f"# Job Hunter Sweep: {profile_title} ({window_label} — {now_str})",
        "",
        "*Estimated Match Scores (0–100) are generated based on profile alignment.*",
        "",
        f"## Ranked Qualifying Jobs (Verified Within {window_label})",
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

    lines.extend(["", "## Detailed Per-Job Breakdown & Descriptions", ""])
    for idx, j in enumerate(verified_jobs, start=1):
        desc_snippet = (j.description[:350] + "...") if len(j.description) > 350 else j.description
        cv_note = f"- **Resume Match**: {j.cv_match_score}% (Optional CV Analysis)\n" if j.cv_match_score else ""
        lines.extend(
            [
                f"### {idx}. [{j.title} — {j.company}]({j.job_url})",
                f"- **Why your profile matches**: {j.why_matches}",
                f"- **Most important skills required**: {j.key_skills_required}",
                cv_note.rstrip() if cv_note else "",
                f"- **Posting time verified**: {'Yes' if j.posted_time_verified else 'No'} ({j.posted_time_raw})",
                f"- **Direct application URL**: [{j.job_url}]({j.job_url})",
                f"- **Concerns**: {j.concerns}",
                f"- **Job Description Summary**: {desc_snippet}",
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

    return "\n".join([line for line in lines if line is not None])


def get_demo_verified_jobs() -> List[JobListing]:
    """Provides seed sample listings when testing offline."""
    return [
        JobListing(
            job_id="demo_1",
            title="Senior Recruitment Consultant / TA Specialist",
            company="Michael Page",
            location="Bengaluru, India",
            work_mode="Hybrid (3 days WFO / 2 days WFH)",
            posted_time_raw="Posted today",
            posted_time_verified=True,
            posting_window="12-24h",
            experience_required="3-7 years",
            salary="INR 18,00,000 - 28,00,000",
            job_url="https://www.iimjobs.com/j/talent-acquisition-role-it-1740105",
            source_portal="iimjobs",
            description=(
                "Michael Page is hiring for a Senior Talent Acquisition Specialist in Bengaluru.\n\n"
                "Key Responsibilities:\n"
                "- Drive end-to-end recruitment life cycle for product engineering and corporate functions.\n"
                "- Partner with international leadership across Singapore, UK, and US offices.\n"
                "- Leverage Boolean search, LinkedIn Recruiter, and talent mapping techniques.\n\n"
                "Requirements:\n"
                "- 3-7 years of recruitment experience in an agency, consultancy, or fast-growing corporate setup.\n"
                "- Strong stakeholder consultation skills and high ownership of talent pipelines."
            ),
        ),
        JobListing(
            job_id="demo_2",
            title="Technical Recruiter (Remote Global Staffing)",
            company="Pinnacle Group",
            location="Worldwide Remote",
            work_mode="Remote / WFH",
            posted_time_raw="2 hours ago",
            posted_time_verified=True,
            posting_window="1-6h",
            experience_required="3-6 years",
            salary="USD $75,000 - $95,000",
            job_url="https://www.remoterocketship.com/company/pinnacle1/jobs/recruiter-india-remote-9/",
            source_portal="Remote Rocketship",
            description=(
                "Pinnacle Group is looking for a Remote US IT Technical Recruiter.\n\n"
                "Key Responsibilities:\n"
                "- Manage full-lifecycle recruiting for strategic Fortune 500 US client accounts.\n"
                "- Source, screen, and submit top-tier software developers, DevOps, and cloud engineers.\n"
                "- Utilize Bullhorn ATS, VMS portals (Fieldglass, Beeline), and AI sourcing automation tools.\n\n"
                "Requirements:\n"
                "- 3+ years in US IT recruitment / staffing.\n"
                "- Expertise in Boolean searches and W2 contract placements."
            ),
        ),
        JobListing(
            job_id="demo_3",
            title="Senior Full Stack Software Engineer (Python & React)",
            company="Automattic",
            location="Remote (Worldwide)",
            work_mode="Remote / WFH",
            posted_time_raw="4 hours ago",
            posted_time_verified=True,
            posting_window="1-6h",
            experience_required="4-8 years",
            salary="USD $120,000 - $160,000",
            job_url="https://automattic.com/work-with-us/",
            source_portal="Google Jobs",
            description=(
                "Automattic is 100% distributed. We are hiring a Senior Software Engineer to build scalable web products.\n\n"
                "Responsibilities:\n"
                "- Architect backend microservices in Python, Node.js, and PostgreSQL.\n"
                "- Develop responsive UI components in React and TypeScript.\n"
                "- Collaborate asynchronously with a globally distributed team.\n\n"
                "Requirements:\n"
                "- 4+ years software development experience.\n"
                "- Strong background in REST APIs, Docker, and AWS."
            ),
        ),
    ]


# =====================================================================
# 8. PIPELINE EXECUTION
# =====================================================================

def run_agent_pipeline(
    queries: Optional[List[str]] = None,
    locations: Optional[List[str]] = None,
    hours_old: int = 24,
    country: str = "India",
    profile: Optional[Dict[str, Any]] = None,
    output_dir: Optional[str] = None,
    stop_checker: Optional[Callable[[], bool]] = None,
    resume_text: Optional[str] = None,
    telegram_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    auto_telegram: bool = False,
) -> Tuple[List[JobListing], str]:
    """
    Executes multi-source scraping, deduplication, match scoring,
    optional resume CV comparison, and automated Telegram alerts.
    """
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
            stop_checker=stop_checker,
        )
    )
    if country.lower() in {"india", "in"} and not (stop_checker and stop_checker()):
        raw_jobs.extend(scrape_iimjobs_feed(max_items=15, max_hours=hours_old, stop_checker=stop_checker))

    if not raw_jobs:
        logger.info("Using seed sample listings (offline or rate limited).")
        raw_jobs = get_demo_verified_jobs()

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    unique_jobs: Dict[str, JobListing] = {}
    for job in raw_jobs:
        if stop_checker and stop_checker():
            break
        dedup_key = JobDatabase.make_dedup_key(job.title, job.company)
        if dedup_key not in unique_jobs:
            scored = evaluate_job_with_llm(job, active_profile)

            # Optional CV Match calculation if resume text is provided
            if resume_text and len(resume_text.strip()) > 30:
                cv_analysis = calculate_resume_match(resume_text, scored, gemini_key=gemini_key)
                if cv_analysis:
                    scored.cv_match_score = cv_analysis.get("score")
                    scored.cv_matching_skills = ", ".join(cv_analysis.get("matching", []))
                    scored.cv_missing_skills = ", ".join(cv_analysis.get("missing", []))
                    scored.cv_tailoring_tips = cv_analysis.get("tips", "")

            unique_jobs[dedup_key] = scored
            db.mark_seen(scored)

    final_jobs = list(unique_jobs.values())
    # Sort by CV score if available, otherwise by profile match score
    if resume_text and any(j.cv_match_score is not None for j in final_jobs):
        final_jobs.sort(key=lambda j: -(j.cv_match_score or j.match_score))
    else:
        final_jobs.sort(key=lambda j: -j.match_score)

    report_md = generate_markdown_report(
        final_jobs,
        profile_title=active_profile.get("name", "Candidate"),
        hours_window=hours_old,
    )

    target_dir = output_dir or "."
    try:
        df_out = pd.DataFrame([asdict(j) for j in final_jobs])
        df_out.to_csv(os.path.join(target_dir, "latest_jobs_24h.csv"), index=False)
        with open(os.path.join(target_dir, "latest_jobs_report.md"), "w", encoding="utf-8") as f:
            f.write(report_md)
    except OSError:
        pass

    # Automated Telegram notification if configured
    tg_token = (telegram_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    tg_chat = (telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    if (auto_telegram or (tg_token and tg_chat)) and tg_token and tg_chat:
        high_matches = [j for j in final_jobs if j.posted_time_verified and j.match_score >= 75]
        if high_matches:
            digest = format_jobs_for_telegram(high_matches, max_jobs=5)
            send_telegram_message(tg_token, tg_chat, digest)

    return final_jobs, report_md


# =====================================================================
# 9. PREMIUM STREAMLIT DASHBOARD (WITH TELEGRAM & CV MATCHER)
# =====================================================================

def run_streamlit_dashboard() -> None:
    """Launches the modern, premium executive Streamlit web dashboard."""
    import streamlit as st

    st.set_page_config(
        page_title="AI Job Hunter Agent | Global & Executive Edition",
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Initialize session states
    if "is_running" not in st.session_state:
        st.session_state["is_running"] = False
    if "resume_text" not in st.session_state:
        st.session_state["resume_text"] = ""
    if "tg_token" not in st.session_state:
        st.session_state["tg_token"] = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if "tg_chat_id" not in st.session_state:
        st.session_state["tg_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", "")
    if "auto_tg" not in st.session_state:
        st.session_state["auto_tg"] = False

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

        html, body, [class*="css"] {
            font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
        }

        .hero-title {
            background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 50%, #ec4899 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 2.2rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            margin-bottom: 0.2rem;
        }

        .hero-sub {
            color: #64748b;
            font-size: 1.0rem;
            font-weight: 500;
            margin-bottom: 1.3rem;
        }

        .kpi-card {
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.02) 100%);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 16px;
            padding: 1.1rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.04);
            text-align: center;
        }

        .kpi-val {
            font-size: 1.9rem;
            font-weight: 800;
            color: #3b82f6;
        }

        .kpi-lbl {
            font-size: 0.8rem;
            color: #94a3b8;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .badge-pill {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 0.02em;
            margin-right: 6px;
        }

        .badge-green  { background: #dcfce7; color: #166534; }
        .badge-blue   { background: #dbeafe; color: #1e40af; }
        .badge-purple { background: #f3e8ff; color: #6b21a8; }
        .badge-orange { background: #ffedd5; color: #9a3412; }
        .badge-cv     { background: #fef08a; color: #854d0e; border: 1px solid #facc15; }
        .badge-tg     { background: #e0f2fe; color: #0369a1; border: 1px solid #7dd3fc; }

        .jd-container {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
            padding: 1rem;
            max-height: 280px;
            overflow-y: auto;
            font-size: 0.9rem;
            line-height: 1.6;
            color: #334155;
            white-space: pre-wrap;
        }

        .cv-box {
            background: #eff6ff;
            border: 1px dashed #3b82f6;
            border-radius: 12px;
            padding: 1rem;
            margin-bottom: 1rem;
        }

        .tg-box {
            background: #f0fdf4;
            border: 1px solid #86efac;
            border-radius: 12px;
            padding: 1.2rem;
            margin-bottom: 1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="hero-title">⚡ Autonomous Global Job Hunter Agent</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-sub">Worldwide multi-portal job scraping (LinkedIn, Indeed, Naukri & Google Jobs) with live Start/Stop controls, customizable posting ages, optional CV match & Telegram alerts.</div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("### 🎮 Agent Controls")
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            start_btn = st.button("▶️ Run Agent", type="primary", use_container_width=True)
        with col_btn2:
            stop_btn = st.button("⏹️ Stop Agent", type="secondary", use_container_width=True)

        if stop_btn:
            st.session_state["is_running"] = False
            st.error("🛑 Agent stopped by user.")
        elif start_btn:
            st.session_state["is_running"] = True

        status_text = "🟢 Hunting Jobs (Active)" if st.session_state.get("is_running") else "⚪ Ready / Idle"
        st.caption(f"**Agent Status:** {status_text}")

        st.divider()
        st.markdown("### 🌍 Global Search Coverage")

        # Global Region Selection
        region_choice = st.selectbox(
            "Select Global Region",
            options=list(GLOBAL_REGIONS.keys()),
            index=0,
            help="Choose a global continental region or worldwide search.",
        )
        region_data = GLOBAL_REGIONS[region_choice]

        # Country selection under region
        country_choice = st.selectbox(
            "Select Target Country",
            options=region_data["countries"],
            index=0,
        )

        final_country = country_choice
        if country_choice == "Custom / Other...":
            final_country = st.text_input("Enter Country Name", value="Germany")

        # Cities & Locations Input (Worldwide / Customizable)
        custom_locs = st.text_input(
            "Target Cities / Locations",
            value=", ".join(region_data["default_cities"][:5]),
            help="Comma-separated cities, provinces, or 'Remote'. Enter any city worldwide!",
        )
        selected_locations = [loc.strip() for loc in custom_locs.split(",") if loc.strip()]

        st.divider()
        st.markdown("### 🎯 Candidate Sector & Profile")
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
            value=float(base_preset.get("experience_years", 3.5)),
            step=0.5,
        )

        custom_skills_input = st.text_input(
            "Core Skills & Technologies",
            value=", ".join(base_preset.get("core_skills", [])),
            help="Comma-separated skills used to match job descriptions.",
        )
        user_skills = [s.strip() for s in custom_skills_input.split(",") if s.strip()]

        custom_titles = st.text_input(
            "Target Job Titles",
            value=", ".join(base_preset.get("default_queries", ["Software Engineer"])),
            help="Comma-separated job titles to hunt for.",
        )
        selected_queries = [t.strip() for t in custom_titles.split(",") if t.strip()]

        st.divider()
        st.markdown("### ⏱️ Customizable Posting Age")
        age_preset = st.selectbox(
            "Select Max Posting Age",
            options=[
                "Last 24 Hours",
                "Last 2 Days (48 Hours)",
                "Last 7 Days (1 Week)",
                "Last 15 Days",
                "Last 30 Days (1 Month)",
                "Custom Days...",
            ],
            index=0,
        )

        if age_preset == "Last 24 Hours":
            hours_old = 24
        elif age_preset == "Last 2 Days (48 Hours)":
            hours_old = 48
        elif age_preset == "Last 7 Days (1 Week)":
            hours_old = 168
        elif age_preset == "Last 15 Days":
            hours_old = 360
        elif age_preset == "Last 30 Days (1 Month)":
            hours_old = 720
        else:
            custom_days = st.number_input("Enter Custom Days", min_value=1, max_value=60, value=10)
            hours_old = int(custom_days * 24)

        min_score_filter = st.slider("Filter: Minimum Match Score", min_value=50, max_value=95, value=65, step=5)

        st.divider()
        with st.expander("📱 Telegram Alert Settings", expanded=False):
            st.markdown("Receive instant mobile alerts on Telegram when top matching jobs are found.")
            sb_token = st.text_input(
                "Telegram Bot Token",
                value=st.session_state.get("tg_token", ""),
                type="password",
                help="Obtain from @BotFather on Telegram.",
                key="sb_tg_token",
            )
            st.session_state["tg_token"] = sb_token

            sb_chat = st.text_input(
                "Telegram Chat ID",
                value=st.session_state.get("tg_chat_id", ""),
                help="Your personal or channel Chat ID (e.g. from @userinfobot).",
                key="sb_tg_chat",
            )
            st.session_state["tg_chat_id"] = sb_chat

            sb_auto = st.checkbox(
                "Auto-alert on sweep (75%+ matches)",
                value=st.session_state.get("auto_tg", False),
                key="sb_tg_auto",
            )
            st.session_state["auto_tg"] = sb_auto

            col_t1, col_t2 = st.columns(2)
            with col_t1:
                if st.button("🧪 Test Bot", use_container_width=True):
                    if not sb_token or not sb_chat:
                        st.error("Enter Bot Token and Chat ID.")
                    else:
                        ok, msg = send_telegram_message(
                            bot_token=sb_token,
                            chat_id=sb_chat,
                            text="⚡ <b>AI Job Hunter Agent Test Alert</b>\n\nYour Telegram bot is successfully connected! You will receive high-match opportunities here.",
                        )
                        if ok:
                            st.success("Test message sent!")
                        else:
                            st.error(msg)
            with col_t2:
                if st.button("📢 Push Jobs", use_container_width=True):
                    current_jobs = st.session_state.get("jobs", [])
                    if not sb_token or not sb_chat:
                        st.error("Enter Bot Token and Chat ID.")
                    elif not current_jobs:
                        st.warning("No jobs to send. Run a sweep first!")
                    else:
                        digest = format_jobs_for_telegram(current_jobs, max_jobs=5)
                        ok, msg = send_telegram_message(sb_token, sb_chat, digest)
                        if ok:
                            st.success("Pushed top matches!")
                        else:
                            st.error(msg)

    # Main Page: Optional CV / Resume Matcher Section
    with st.expander("📄 Match Your CV / Resume with Jobs (Optional)", expanded=False):
        st.markdown(
            """
            Upload or paste your CV to calculate an **individual Resume Match Score (0–100%)** for each job,
            see which skills overlap, identify missing JD keywords, and get ATS optimization tips.
            *This section is completely optional—the agent works perfectly even without a CV!*
            """
        )
        cv_col1, cv_col2 = st.columns([1, 1])
        with cv_col1:
            uploaded_file = st.file_uploader(
                "Upload Your CV / Resume (PDF, DOCX, TXT)",
                type=["pdf", "docx", "txt"],
                help="Optional: Upload your resume file to compare against scraped job postings.",
            )
            if uploaded_file is not None:
                parsed_cv = extract_text_from_upload(uploaded_file)
                if parsed_cv:
                    st.session_state["resume_text"] = parsed_cv
                    st.success(f"✅ Loaded resume: `{uploaded_file.name}` ({len(parsed_cv.split())} words)")
        with cv_col2:
            pasted_cv = st.text_area(
                "Or Paste Your CV / Resume Text:",
                value=st.session_state.get("resume_text", ""),
                height=130,
                placeholder="Paste work experience, skills, and summary here...",
            )
            if pasted_cv:
                st.session_state["resume_text"] = pasted_cv

        if st.session_state.get("resume_text"):
            st.info("🎯 Resume matching is **ACTIVE**. Jobs will display both candidate profile and direct CV match scores.")
            if st.button("Clear CV"):
                st.session_state["resume_text"] = ""
                st.rerun()

    user_profile = {
        "name": sector_choice,
        "experience_years": user_exp,
        "core_skills": user_skills,
        "default_queries": selected_queries,
        "default_locations": selected_locations,
    }

    # Stop checker function
    def stop_signal() -> bool:
        return not st.session_state.get("is_running", True)

    # Trigger job search on Start button or initial page load
    if start_btn:
        st.session_state["is_running"] = True
        with st.spinner(
            f"🤖 Hunting {region_choice} ({final_country}) for jobs from the last "
            f"{hours_old // 24 if hours_old >= 48 else hours_old} {'days' if hours_old >= 48 else 'hours'}..."
        ):
            jobs, report_md = run_agent_pipeline(
                queries=selected_queries,
                locations=selected_locations,
                hours_old=hours_old,
                country=final_country,
                profile=user_profile,
                stop_checker=stop_signal,
                resume_text=st.session_state.get("resume_text"),
                telegram_token=st.session_state.get("tg_token"),
                telegram_chat_id=st.session_state.get("tg_chat_id"),
                auto_telegram=st.session_state.get("auto_tg", False),
            )
            st.session_state["jobs"] = jobs
            st.session_state["report_md"] = report_md
            st.session_state["hours_old"] = hours_old
            st.session_state["is_running"] = False
    elif "jobs" not in st.session_state:
        # Load demo initial data
        demo_jobs = get_demo_verified_jobs()
        st.session_state["jobs"] = demo_jobs
        st.session_state["report_md"] = generate_markdown_report(demo_jobs, hours_window=hours_old)
        st.session_state["hours_old"] = hours_old

    jobs: List[JobListing] = st.session_state.get("jobs", [])
    verified = [j for j in jobs if j.posted_time_verified and j.match_score >= min_score_filter]

    # Executive KPI Metric Cards
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f'<div class="kpi-card"><div class="kpi-val">{len(verified)}</div><div class="kpi-lbl">Verified Within Window</div></div>',
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
            f'<div class="kpi-card"><div class="kpi-val">{remote_cnt}</div><div class="kpi-lbl">Remote / Global WFH</div></div>',
            unsafe_allow_html=True,
        )
    with c4:
        avg_score = int(sum(j.match_score for j in verified) / max(1, len(verified)))
        st.markdown(
            f'<div class="kpi-card"><div class="kpi-val">{avg_score}%</div><div class="kpi-lbl">Avg Alignment</div></div>',
            unsafe_allow_html=True,
        )

    st.write("")

    # Modern Navigation Tabs
    tab_cards, tab_cv, tab_tg, tab_analytics, tab_table, tab_pitch = st.tabs(
        [
            "🔥 Verified Opportunities & Full JDs",
            "🎯 CV vs Job Gap Analysis (Optional)",
            "📱 Telegram Alerts & Bot Settings",
            "📊 Global Market Insights",
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
                cv_badge = (
                    f'<span class="badge-pill badge-cv">📄 CV Match: {j.cv_match_score}%</span>'
                    if j.cv_match_score is not None else ""
                )

                with st.expander(
                    f"#{idx}  •  {j.title}  —  {j.company}  |  {j.match_score}/100 Match  ({j.posted_time_raw})",
                    expanded=(idx <= 3),
                ):
                    col_details, col_action = st.columns([3, 1])
                    with col_details:
                        st.markdown(
                            f"""
                            <span class="badge-pill {badge_class}">⭐ {j.match_score}/100 Profile Match</span>
                            {cv_badge}
                            <span class="badge-pill badge-blue">🏢 {j.source_portal}</span>
                            {window_badge}
                            {remote_badge}
                            """,
                            unsafe_allow_html=True,
                        )
                        st.write(f"📍 **Location:** {j.location} ({j.work_mode})  •  ⏳ **Experience:** {j.experience_required}  •  💰 **Salary:** {j.salary}")
                        st.markdown(f"**🎯 Why You Match:** {j.why_matches}")
                        st.markdown(f"**🔑 Key Skills Looked For:** `{j.key_skills_required}`")

                        # Optional CV Match details if computed
                        if j.cv_match_score is not None:
                            st.markdown(
                                f"""
                                <div class="cv-box">
                                    <b>📄 Personalized CV Alignment:</b> {j.cv_match_score}%<br>
                                    <b>✅ Matched Skills in Your CV:</b> {j.cv_matching_skills or 'General alignment'}<br>
                                    <b>⚠️ Keywords to Add:</b> {j.cv_missing_skills or 'None, great coverage!'}<br>
                                    <b>💡 ATS Tailoring Tip:</b> {j.cv_tailoring_tips}
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )

                        if j.concerns and j.concerns != "None identified":
                            st.caption(f"⚠️ **Note:** {j.concerns}")
                    with col_action:
                        st.write("")
                        st.link_button("🚀 Direct Apply Now", j.job_url, use_container_width=True)
                        if st.button("📱 Send to Telegram", key=f"card_tg_{j.job_id}", use_container_width=True):
                            tg_tok = st.session_state.get("tg_token")
                            tg_ch = st.session_state.get("tg_chat_id")
                            if not tg_tok or not tg_ch:
                                st.error("Please configure your Telegram Bot Token & Chat ID in the sidebar first.")
                            else:
                                single_card_msg = format_single_job_for_telegram(j)
                                ok, msg = send_telegram_message(tg_tok, tg_ch, single_card_msg)
                                if ok:
                                    st.success("Sent to Telegram!")
                                else:
                                    st.error(msg)
                        st.caption(f"Portal: {j.source_portal}")

                    # Full Job Description Viewer
                    st.markdown("#### 📄 Full Job Description")
                    if j.description and len(j.description) > 30:
                        st.markdown(f'<div class="jd-container">{j.description}</div>', unsafe_allow_html=True)
                    else:
                        st.info("Full description text available directly on the employer portal via Direct Apply.")

    with tab_cv:
        st.subheader("🎯 Deep CV vs Job Description Gap Analysis")
        if not st.session_state.get("resume_text"):
            st.info("💡 To use this feature, upload your CV or paste your resume text in the **'📄 Match Your CV / Resume with Jobs'** expander above.")
        elif not verified:
            st.info("No jobs available for analysis. Click '▶️ Run Agent' to search.")
        else:
            job_options = {f"{j.title} at {j.company} ({j.location})": j for j in verified}
            selected_key = st.selectbox("Select Job to Compare Against Your CV", list(job_options.keys()))
            target_job = job_options[selected_key]

            st.write(f"### Evaluating CV for: **{target_job.title}** at **{target_job.company}**")
            analysis = calculate_resume_match(
                st.session_state["resume_text"],
                target_job,
                gemini_key=os.getenv("GEMINI_API_KEY", "").strip(),
            )

            col_score, col_feedback = st.columns([1, 2])
            with col_score:
                st.metric("Resume Match Score", f"{analysis.get('score', 75)}%", delta="ATS Ready" if analysis.get('score', 75) >= 80 else "Needs Customization")
            with col_feedback:
                st.markdown(f"**💡 Actionable Advice:** {analysis.get('tips')}")

            col_match, col_miss = st.columns(2)
            with col_match:
                st.markdown("#### ✅ Matching Strengths in Your CV")
                if analysis.get("matching"):
                    for skill in analysis["matching"]:
                        st.write(f"- {skill}")
                else:
                    st.write("General domain alignment detected.")
            with col_miss:
                st.markdown("#### ⚠️ Keywords Missing from Your CV")
                if analysis.get("missing"):
                    for skill in analysis["missing"]:
                        st.write(f"- {skill}")
                else:
                    st.write("Excellent keyword coverage! All key terms are in your CV.")

    with tab_tg:
        st.subheader("📱 Telegram Alert & Notification Center")
        st.markdown(
            """
            Connect your personal Telegram account, group, or broadcast channel to receive instant
            alerts as soon as new high-scoring job openings are discovered.
            """
        )

        col_cfg, col_preview = st.columns([1, 1])
        with col_cfg:
            st.markdown("#### ⚙️ Bot Credentials & Preferences")
            tab_tg_token = st.text_input(
                "Telegram Bot Token",
                value=st.session_state.get("tg_token", ""),
                type="password",
                help="Created via @BotFather on Telegram (e.g., 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ).",
                key="tab_tg_token_input",
            )
            st.session_state["tg_token"] = tab_tg_token

            tab_tg_chat = st.text_input(
                "Telegram Chat ID",
                value=st.session_state.get("tg_chat_id", ""),
                help="Your numeric user ID (from @userinfobot) or @channelusername.",
                key="tab_tg_chat_input",
            )
            st.session_state["tg_chat_id"] = tab_tg_chat

            tab_tg_auto = st.checkbox(
                "Automatically push top matches (75%+ score) after every sweep",
                value=st.session_state.get("auto_tg", False),
                key="tab_tg_auto_input",
            )
            st.session_state["auto_tg"] = tab_tg_auto

            st.write("")
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                if st.button("🧪 Send Test Message", type="secondary", use_container_width=True):
                    if not tab_tg_token or not tab_tg_chat:
                        st.error("Please enter both Bot Token and Chat ID.")
                    else:
                        with st.spinner("Connecting to Telegram..."):
                            ok, msg = send_telegram_message(
                                bot_token=tab_tg_token,
                                chat_id=tab_tg_chat,
                                text="⚡ <b>Telegram Bot Connected!</b>\n\nYour Autonomous Job Hunter Agent is ready to send instant notifications.",
                            )
                            if ok:
                                st.success("✅ Test message received on Telegram!")
                            else:
                                st.error(f"❌ Connection failed: {msg}")

            with col_b2:
                if st.button("📢 Broadcast Top 5 Matches", type="primary", use_container_width=True):
                    if not tab_tg_token or not tab_tg_chat:
                        st.error("Please enter both Bot Token and Chat ID.")
                    elif not verified:
                        st.warning("No verified jobs available. Click '▶️ Run Agent' to scrape jobs first.")
                    else:
                        with st.spinner("Broadcasting top opportunities..."):
                            digest = format_jobs_for_telegram(verified, max_jobs=5)
                            ok, msg = send_telegram_message(tab_tg_token, tab_tg_chat, digest)
                            if ok:
                                st.success(f"✅ Successfully sent top {min(5, len(verified))} jobs to Telegram!")
                            else:
                                st.error(f"❌ Failed to broadcast: {msg}")

            st.markdown(
                """
                ---
                ##### 💡 How to set up Telegram in 2 minutes:
                1. Open Telegram and search for **[@BotFather](https://t.me/BotFather)**.
                2. Send `/newbot`, give it a name, and copy the **HTTP API token**.
                3. Search for **[@userinfobot](https://t.me/userinfobot)** and press Start to get your **numeric Chat ID**.
                4. Paste both values above and click **🧪 Send Test Message**!
                """
            )

        with col_preview:
            st.markdown("#### 💬 Live Telegram Message Preview")
            if verified:
                sample_preview = format_jobs_for_telegram(verified[:2], max_jobs=2)
                st.markdown(
                    f'<div class="tg-box">{sample_preview}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.info("Run a job sweep to see a live preview of the Telegram broadcast digest.")

    with tab_analytics:
        st.subheader("📊 Global Job Market & Hiring Insights")
        if verified:
            col_chart1, col_chart2 = st.columns(2)
            with col_chart1:
                st.write("**Top Hiring Hubs & Cities**")
                loc_counts = pd.Series([j.location.split(",")[0].strip() for j in verified]).value_counts().head(8)
                st.bar_chart(loc_counts)
            with col_chart2:
                st.write("**Work Mode Breakdown**")
                mode_counts = pd.Series([j.work_mode for j in verified]).value_counts()
                st.bar_chart(mode_counts)

            st.write("**Match Alignment Curve**")
            scores = [j.match_score for j in verified]
            st.line_chart(scores)

    with tab_table:
        st.subheader("📋 Ranked Global Opportunities Grid")
        search_query = st.text_input("🔍 Quick filter by keyword, company, city, or title:")
        filtered_jobs = [
            j for j in verified
            if search_query.lower() in f"{j.title} {j.company} {j.location}".lower()
        ] if search_query else verified

        df_display = pd.DataFrame(
            [
                {
                    "Rank": i,
                    "Profile Match": f"{j.match_score}/100",
                    "CV Match": f"{j.cv_match_score}%" if j.cv_match_score is not None else "N/A",
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
            label="📥 Download Opportunities as CSV (with Full JDs)",
            data=pd.DataFrame([asdict(j) for j in verified]).to_csv(index=False),
            file_name="verified_jobs.csv",
            mime="text/csv",
        )

    with tab_pitch:
        st.subheader("✍️ 1-Click AI Outreach Pitch Generator")
        st.caption("Generate a personalized LinkedIn InMail or cold email message for any hiring manager.")
        if verified:
            job_choices = {f"{j.title} at {j.company}": j for j in verified}
            selected_job_key = st.selectbox("Select Target Job for Pitch", list(job_choices.keys()))
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
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Maximum posting age in hours (e.g., 24, 48, 168 for 7 days, 360 for 15 days, 720 for 30 days).",
    )
    parser.add_argument(
        "--country",
        type=str,
        default="India",
        help="Target country for job search (e.g., USA, India, UK, Canada, Australia, Germany).",
    )
    parser.add_argument(
        "--telegram-token",
        type=str,
        default=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        help="Telegram Bot Token for alerts.",
    )
    parser.add_argument(
        "--telegram-chat-id",
        type=str,
        default=os.getenv("TELEGRAM_CHAT_ID", ""),
        help="Telegram Chat ID for alerts.",
    )
    parser.add_argument(
        "--notify-telegram",
        action="store_true",
        help="Force send Telegram notification with top matches.",
    )
    args, _ = parser.parse_known_args()

    in_streamlit = any("streamlit" in k.lower() for k in os.environ)
    if args.cli or not in_streamlit:
        cli_token = args.telegram_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        cli_chat = args.telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        should_tg = args.notify_telegram or bool(cli_token and cli_chat)

        jobs_list, md_output = run_agent_pipeline(
            hours_old=args.hours,
            country=args.country,
            telegram_token=cli_token,
            telegram_chat_id=cli_chat,
            auto_telegram=should_tg,
        )
        print(md_output)
        logger.info("Completed sweep: %d verified jobs processed.", len(jobs_list))
    else:
        run_streamlit_dashboard()
