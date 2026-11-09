"""
StudyPlan AI — Automated Job Search & Cover Letter Generator
Scrapes AI PM roles from job boards, scores fit, generates tailored cover letters,
and sends a daily digest email via Resend.
Runs via GitHub Actions every weekday at 7am UK time.
"""

import os
import json
import csv
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, date

# ── CONFIG ────────────────────────────────────────────────────────────────────
RESEND_API_KEY   = os.environ["RESEND_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
FROM_EMAIL       = "StudyPlan AI Jobs <abdulrahman@studyplanai.co.uk>"
TO_EMAIL         = "aliubdlrhmn@gmail.com"
SEEN_LOG         = "seen_jobs.csv"
DAILY_LIMIT      = 10   # max jobs to include in digest

CANDIDATE = {
    "name": "Abdulrahman Aliu",
    "title": "AI Product Manager & Founder",
    "summary": (
        "AI Product Manager and Founder with a proven track record of building and shipping "
        "live AI-powered products and leading business systems transformation. Founder of "
        "StudyPlan AI Ltd — a live AI platform integrating LLMs, full-stack development, "
        "and consent-based wellbeing monitoring. Experienced in Agile and PRINCE2 environments "
        "with 5+ years as a Business Analyst. Technically fluent in LLMs, agentic systems, "
        "Next.js, Supabase, Python, and CI/CD pipelines."
    ),
    "skills": [
        "AI product development", "LLM integration", "Prompt engineering",
        "Claude API", "Next.js", "Python", "Supabase", "GitHub Actions",
        "Agile", "PRINCE2", "Stakeholder management", "UAT", "GDPR",
        "Product roadmap", "Requirements gathering", "Process improvement"
    ],
    "locations": ["London", "Milton Keynes", "Birmingham", "Remote"],
    "salary_min": 55000,
    "salary_max": 80000,
    "avoid": ["defence", "gambling", "betting", "military", "weapons", "aerospace defence"]
}

# ── JOB SEARCH QUERIES ────────────────────────────────────────────────────────
SEARCH_QUERIES = [
    "AI Product Manager London",
    "AI Product Manager Remote UK",
    "AI PM product manager artificial intelligence London",
    "AI product owner London Birmingham",
    "LLM product manager UK",
    "Machine learning product manager London",
]

# ── LOAD SEEN JOBS ────────────────────────────────────────────────────────────
def load_seen():
    seen = set()
    if os.path.exists(SEEN_LOG):
        with open(SEEN_LOG, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if row:
                    seen.add(row[0].strip())
    return seen

def log_seen(job_id, title, company):
    with open(SEEN_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([job_id, title, company, datetime.now().strftime("%Y-%m-%d")])

# ── SCRAPE JOBS FROM REED.CO.UK ───────────────────────────────────────────────
def search_reed(query, results_to_fetch=10):
    """Search Reed.co.uk API for jobs matching query."""
    # Reed API - free to use, no auth needed for basic search
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.reed.co.uk/api/1.0/search?keywords={encoded_query}&locationName=London&distancefromlocation=50&minimumSalary=50000&resultsToTake={results_to_fetch}"

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "StudyPlanAI-JobBot/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("results", [])
    except Exception as e:
        print(f"  Reed search error for '{query}': {e}")
        return []

def search_adzuna(query):
    """Search Adzuna API for UK jobs."""
    app_id  = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        return []

    encoded_query = urllib.parse.quote(query)
    url = (
        f"https://api.adzuna.com/v1/api/jobs/gb/search/1"
        f"?app_id={app_id}&app_key={app_key}"
        f"&results_per_page=10&what={encoded_query}"
        f"&salary_min=50000&content-type=application/json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StudyPlanAI-JobBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("results", [])
    except Exception as e:
        print(f"  Adzuna search error for '{query}': {e}")
        return []

# ── NORMALISE JOBS ────────────────────────────────────────────────────────────
def normalise_reed(job):
    return {
        "id": f"reed_{job.get('jobId', '')}",
        "title": job.get("jobTitle", ""),
        "company": job.get("employerName", ""),
        "location": job.get("locationName", ""),
        "salary": f"£{job.get('minimumSalary', 0):,.0f} - £{job.get('maximumSalary', 0):,.0f}" if job.get("minimumSalary") else "Not stated",
        "description": job.get("jobDescription", ""),
        "url": job.get("jobUrl", f"https://www.reed.co.uk/jobs/{job.get('jobId', '')}"),
        "date_posted": job.get("date", ""),
        "source": "Reed"
    }

def normalise_adzuna(job):
    sal_min = job.get("salary_min", 0) or 0
    sal_max = job.get("salary_max", 0) or 0
    return {
        "id": f"adzuna_{job.get('id', '')}",
        "title": job.get("title", ""),
        "company": job.get("company", {}).get("display_name", ""),
        "location": job.get("location", {}).get("display_name", ""),
        "salary": f"£{sal_min:,.0f} - £{sal_max:,.0f}" if sal_min else "Not stated",
        "description": job.get("description", ""),
        "url": job.get("redirect_url", ""),
        "date_posted": job.get("created", ""),
        "source": "Adzuna"
    }

# ── FILTER JOBS ───────────────────────────────────────────────────────────────
def is_relevant(job):
    text = (job["title"] + " " + job["company"] + " " + job["description"]).lower()

    # Exclude blacklisted sectors
    for bad in CANDIDATE["avoid"]:
        if bad in text:
            return False, f"Excluded: contains '{bad}'"

    # Must mention AI/product/technology
    keywords = ["ai", "artificial intelligence", "product manager", "product owner",
                 "llm", "machine learning", "data", "digital", "tech", "software"]
    if not any(k in text for k in keywords):
        return False, "Not relevant: no AI/product keywords"

    return True, "OK"

def score_job(job):
    """Score 0-100 based on keyword match with candidate profile."""
    text = (job["title"] + " " + job["description"]).lower()
    score = 0

    high_value = ["ai product", "llm", "product manager", "artificial intelligence",
                  "machine learning", "ai pm", "ai product manager"]
    for kw in high_value:
        if kw in text:
            score += 15

    medium_value = ["agile", "stakeholder", "roadmap", "python", "saas", "fintech",
                    "edtech", "healthtech", "startup", "scale-up", "b2b"]
    for kw in medium_value:
        if kw in text:
            score += 5

    location_match = any(loc.lower() in text for loc in CANDIDATE["locations"])
    if location_match:
        score += 10

    return min(score, 100)

# ── GENERATE COVER LETTER VIA CLAUDE ─────────────────────────────────────────
def generate_cover_letter(job):
    prompt = f"""You are writing a professional cover letter for {CANDIDATE['name']}, an {CANDIDATE['title']}.

Candidate background:
{CANDIDATE['summary']}

Key skills: {', '.join(CANDIDATE['skills'])}

Job details:
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
Description: {job['description'][:1500]}

Write a 4-paragraph cover letter that:
1. Opens with genuine interest in this specific role and company
2. Highlights the most relevant experience (StudyPlan AI founder + BA background)
3. Maps specific skills to the job requirements
4. Closes with a clear call to action

Keep it under 350 words. Professional but direct tone. No fluff. Start with "Dear Hiring Team,"
Do not include address headers or date - just the letter body."""

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 600,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
        return data["content"][0]["text"]

# ── BUILD EMAIL ───────────────────────────────────────────────────────────────
def build_digest_email(jobs_with_letters):
    today = date.today().strftime("%A %d %B %Y")
    count = len(jobs_with_letters)

    html = f"""<!DOCTYPE html>
<html><body style="font-family: Arial, sans-serif; font-size: 15px; color: #1a1a1a; max-width: 700px; margin: 0 auto; padding: 20px;">

<h2 style="color: #0F172A; border-bottom: 2px solid #2A9D8F; padding-bottom: 8px;">
  🎯 Daily Job Digest — {today}
</h2>

<p>Found <strong>{count} new AI PM role{"s" if count != 1 else ""}</strong> matching your profile today.</p>

"""

    for i, (job, cover_letter, score) in enumerate(jobs_with_letters, 1):
        fit_color = "#16a34a" if score >= 60 else "#d97706" if score >= 40 else "#6b7280"
        html += f"""
<div style="border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin-bottom: 24px;">

  <table width="100%"><tr>
    <td><h3 style="margin: 0; color: #0F172A;">{i}. {job['title']}</h3></td>
    <td align="right"><span style="background: {fit_color}; color: white; padding: 4px 10px; border-radius: 12px; font-size: 13px;">
      Fit: {score}%
    </span></td>
  </tr></table>

  <p style="margin: 6px 0; color: #475569;">
    <strong>{job['company']}</strong> · {job['location']} · {job['salary']} · {job['source']}
  </p>

  <p><a href="{job['url']}" style="background: #2A9D8F; color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-size: 14px;">
    View & Apply →
  </a></p>

  <div style="background: #f8fafc; border-left: 3px solid #2A9D8F; padding: 16px; margin-top: 16px; border-radius: 4px;">
    <p style="margin: 0 0 8px; font-weight: bold; color: #0F172A;">📝 Ready-to-use cover letter:</p>
    <p style="margin: 0; white-space: pre-line; font-size: 14px; line-height: 1.6;">{cover_letter}</p>
  </div>

</div>
"""

    html += f"""
<hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;">
<p style="color: #94A3B8; font-size: 13px;">
  Sent by StudyPlan AI Job Bot · {today}<br>
  CV attached · Reply to this email to unsubscribe
</p>

</body></html>"""

    return html

# ── SEND EMAIL ────────────────────────────────────────────────────────────────
def send_digest(html, job_count):
    today = date.today().strftime("%d %b %Y")
    subject = f"🎯 {job_count} New AI PM Role{'s' if job_count != 1 else ''} — {today}"

    payload = json.dumps({
        "from": FROM_EMAIL,
        "to": [TO_EMAIL],
        "subject": subject,
        "html": html
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Job search starting...")

    seen = load_seen()
    all_jobs = []

    # Scrape Reed
    for query in SEARCH_QUERIES:
        print(f"  Searching Reed: '{query}'...")
        raw = search_reed(query)
        for r in raw:
            job = normalise_reed(r)
            if job["id"] not in seen:
                relevant, reason = is_relevant(job)
                if relevant:
                    job["score"] = score_job(job)
                    all_jobs.append(job)
                else:
                    print(f"    Skipped '{job['title']}' at {job['company']}: {reason}")

        # Also try Adzuna if keys provided
        raw_az = search_adzuna(query)
        for r in raw_az:
            job = normalise_adzuna(r)
            if job["id"] not in seen:
                relevant, reason = is_relevant(job)
                if relevant:
                    job["score"] = score_job(job)
                    all_jobs.append(job)

    # Deduplicate by ID
    seen_ids = set()
    unique_jobs = []
    for job in all_jobs:
        if job["id"] not in seen_ids:
            seen_ids.add(job["id"])
            unique_jobs.append(job)

    # Sort by score, take top N
    unique_jobs.sort(key=lambda x: x["score"], reverse=True)
    batch = unique_jobs[:DAILY_LIMIT]

    if not batch:
        print("No new matching jobs found today.")
        return

    print(f"\nFound {len(batch)} new jobs. Generating cover letters...")

    jobs_with_letters = []
    for job in batch:
        print(f"  Generating cover letter for: {job['title']} at {job['company']}...")
        try:
            cover_letter = generate_cover_letter(job)
            jobs_with_letters.append((job, cover_letter, job["score"]))
            log_seen(job["id"], job["title"], job["company"])
        except Exception as e:
            print(f"  Failed to generate letter for {job['title']}: {e}")
            jobs_with_letters.append((job, "Cover letter generation failed — please write manually.", job["score"]))
            log_seen(job["id"], job["title"], job["company"])

    print(f"\nSending digest email with {len(jobs_with_letters)} roles...")
    html = build_digest_email(jobs_with_letters)
    send_digest(html, len(jobs_with_letters))
    print(f"✓ Digest sent to {TO_EMAIL}")
    print(f"Done. {len(jobs_with_letters)} jobs processed.")

if __name__ == "__main__":
    main()
