import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
import anthropic

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY")
MAILCHIMP_API_KEY     = os.getenv("MAILCHIMP_API_KEY")
MAILCHIMP_AUDIENCE_ID = os.getenv("MAILCHIMP_AUDIENCE_ID")
MAILCHIMP_SERVER      = os.getenv("MAILCHIMP_SERVER", "us1")
MAILCHIMP_BASE_URL    = f"https://{MAILCHIMP_SERVER}.api.mailchimp.com/3.0"

# Issue count stored in a file in the same directory as this script
ISSUE_COUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "newsletter_issue_count.json")

CONTENT_PILLARS = [
    "AI automation and execution",
    "The new corporate structure",
    "Human elevation over replacement",
    "The Golden Age of Technology",
    "Founder liberation",
    "rawstate.ai case studies and behind the build",
    "AI tools and system design",
]

NEWSLETTER_SYSTEM_PROMPT = """
You write the weekly newsletter called "The Raw State" for rawstate.ai.

ABOUT RAWSTATE.AI:
rawstate.ai is an AI execution firm that builds and deploys custom intelligent systems
for small businesses and founder-led companies. The parent company is Gradual Holdings Inc. —
a holding company being built to define how corporations are structured at the dawn of
the Golden Age of Technology.

The newsletter is called The Raw State — the unfiltered dispatch on AI, new corporate
structures, and what actually changes.

VOICE:
- The voice of a founder who thinks in systems and writes like a journalist
- Sharp, considered, slightly provocative
- No jargon. No filler. Every sentence earns its place.
- Short enough to read in 3 minutes, dense enough to think about all day

FORMAT — follow this exactly:
- 300-400 words total
- NO headers, NO bullet points, NO lists — prose only
- Structure:
  1. Opening observation that creates tension (40-60 words)
  2. Build the argument (180-220 words)
  3. Land the implication (60-80 words)
  4. One closing sentence that makes the reader wonder what comes next
- End with exactly:
  Elsie
  rawstate.ai
  https://www.rawstate.ai

EVOLUTION RULE:
- Issues 1-20: Write purely from rawstate.ai perspective. No mention of Gradual Holdings.
- Issues 21-40: Occasionally reference "the larger system we are building" without naming it directly.
- Issues 41+: Begin referencing Gradual Holdings Inc. by name as the parent entity.
""".strip()


# ── Issue counter ─────────────────────────────────────────────────────────────
def get_issue_number():
    if os.path.exists(ISSUE_COUNT_FILE):
        with open(ISSUE_COUNT_FILE, "r") as f:
            data = json.load(f)
            return data.get("count", 0) + 1
    return 1


def save_issue_number(n):
    with open(ISSUE_COUNT_FILE, "w") as f:
        json.dump({"count": n}, f)


# ── Pillar selection ──────────────────────────────────────────────────────────
def get_this_weeks_pillar(issue_number):
    return CONTENT_PILLARS[(issue_number - 1) % len(CONTENT_PILLARS)]


# ── Claude: Write issue ───────────────────────────────────────────────────────
def write_issue(client, pillar, issue_number):
    if issue_number <= 20:
        evolution_note = "This is an early issue (1-20). Write purely from rawstate.ai perspective."
    elif issue_number <= 40:
        evolution_note = "This is a mid-stage issue (21-40). You may occasionally hint at a larger system being built."
    else:
        evolution_note = "This is a mature issue (41+). You may reference Gradual Holdings Inc. by name."

    prompt = f"""Write issue #{issue_number} of The Raw State newsletter.

Topic / content pillar: {pillar}

{evolution_note}

Write the full newsletter issue now. Remember: 300-400 words, prose only, no headers or bullets.
End with:
Elsie
rawstate.ai
https://www.rawstate.ai"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        system=NEWSLETTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


def generate_subject(client, pillar):
    prompt = f"""Generate a compelling email subject line for a newsletter issue about: {pillar}

The subject should be:
- 5-8 words maximum
- Intriguing, not clickbait
- Sounds like a journalist wrote it
- Makes the reader want to open immediately
- No emojis

Reply with only the subject line, nothing else."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


# ── HTML template ─────────────────────────────────────────────────────────────
def text_to_html(text, issue_number):
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    html_paras = "\n".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ font-family: Georgia, serif; max-width: 600px; margin: 0 auto; padding: 40px 20px; color: #1a1a1a; line-height: 1.7; }}
  p {{ margin: 0 0 20px 0; font-size: 16px; }}
  .header {{ font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 40px; }}
  .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; font-size: 13px; color: #888; }}
</style>
</head>
<body>
  <div class="header">The Raw State &nbsp;·&nbsp; Issue #{issue_number}</div>
  {html_paras}
  <div class="footer">
    You are receiving this because you subscribed to The Raw State.<br>
    rawstate.ai &nbsp;·&nbsp; <a href="https://www.rawstate.ai">www.rawstate.ai</a>
  </div>
</body>
</html>"""


# ── Mailchimp: Create and send ────────────────────────────────────────────────
def create_and_send_campaign(subject, html_content, issue_number):
    auth = ("anystring", MAILCHIMP_API_KEY)
    headers = {"Content-Type": "application/json"}

    campaign_payload = {
        "type": "regular",
        "recipients": {"list_id": MAILCHIMP_AUDIENCE_ID},
        "settings": {
            "subject_line": subject,
            "preview_text": f"The Raw State — Issue #{issue_number}",
            "title": f"The Raw State Issue #{issue_number}",
            "from_name": "Elsie from rawstate.ai",
            "reply_to": "welcome@gradualholdings.com",
        }
    }

    resp = requests.post(
        f"{MAILCHIMP_BASE_URL}/campaigns",
        auth=auth, headers=headers, json=campaign_payload, timeout=30
    )
    if resp.status_code not in [200, 201]:
        raise Exception(f"Mailchimp create error {resp.status_code}: {resp.json()}")

    campaign_id = resp.json()["id"]
    print(f"  → Campaign created: {campaign_id}")

    resp = requests.put(
        f"{MAILCHIMP_BASE_URL}/campaigns/{campaign_id}/content",
        auth=auth, headers=headers, json={"html": html_content}, timeout=30
    )
    if resp.status_code not in [200, 201]:
        raise Exception(f"Mailchimp content error {resp.status_code}: {resp.json()}")

    resp = requests.post(
        f"{MAILCHIMP_BASE_URL}/campaigns/{campaign_id}/actions/send",
        auth=auth, headers=headers, timeout=30
    )
    if resp.status_code not in [200, 204]:
        raise Exception(f"Mailchimp send error {resp.status_code}: {resp.text}")

    print(f"  → Sent successfully")
    return campaign_id


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] The Raw State newsletter running...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    issue_number = get_issue_number()
    pillar = get_this_weeks_pillar(issue_number)
    print(f"  → Issue #{issue_number} — Pillar: {pillar}")

    print(f"  → Writing issue...")
    content = write_issue(client, pillar, issue_number)

    print(f"  → Generating subject...")
    subject = generate_subject(client, pillar)
    print(f"  → Subject: {subject}")

    html_content = text_to_html(content, issue_number)

    print(f"  → Sending to Mailchimp...")
    try:
        campaign_id = create_and_send_campaign(subject, html_content, issue_number)
        save_issue_number(issue_number)
        print(f"  → Issue #{issue_number} sent. Campaign: {campaign_id}")
    except Exception as e:
        print(f"  → Failed: {e}")

    print(f"  → Done.\n")


if __name__ == "__main__":
    run()
