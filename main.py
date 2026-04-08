"""
gradual_pipeline/main.py

LinkedIn automation pipeline for Elsie Gomes / Gradual Holdings Inc.
Reads topics.csv, generates a post + image, posts to LinkedIn, and schedules itself
to run every 2 days at 08:00 America/Toronto.

Usage:
  python main.py            # Start the APScheduler (runs on a 2-day interval)
  python main.py --run-now  # Execute one pipeline run immediately (useful for testing)
"""

import os
import csv
import sys
import time
import logging
import requests
import anthropic
from datetime import datetime, timedelta
from io import BytesIO

import pytz
from PIL import Image
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gradual_pipeline")

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
KIE_API_KEY          = os.getenv("KIE_API_KEY", "")
LINKEDIN_ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN", "")

TOPICS_CSV  = "topics.csv"
LOG_CSV     = "log.csv"
LOGO_PATH   = "logo.png"
TEMP_IMAGE  = "temp_post_image.png"

TORONTO_TZ = pytz.timezone("America/Toronto")

# Voice profile injected into every Claude prompt
VOICE_PROFILE = """
You are Elsie Gomes, founder of Gradual Holdings Inc., a multi-industry holding company
starting with AI integration consultancy.

VOICE RULES — follow every one of these exactly:
- Tone: founder building in public, raw and strategic, never corporate.
- Style: storytelling first. Open with a real moment or bold statement, then deliver the insight.
- Sentence rhythm: short punchy sentences mixed with longer ones. Vary deliberately.
- No hype, no buzzwords — facts from someone doing the thing.
- NEVER use: game-changer, in today's rapidly evolving landscape, leverage synergies, disruption.
- End with something that lands — a truth, a tension, a reframe. NOT a call to action.
- Hashtags: exactly 1 topic-relevant hashtag + #GradualHoldings + #HumanAI, placed at the very end.
- Length: 300-500 words depending on topic depth. Never go under 300 or over 500.
""".strip()


# ---------------------------------------------------------------------------
# Step 1 — Read next queued topic
# ---------------------------------------------------------------------------

def read_next_queued_topic():
    """Return (row_index, row_dict, all_rows) for the first queued row, or (None, None, rows)."""
    with open(TOPICS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows):
        if row["status"].strip().lower() == "queued":
            return i, row, rows

    return None, None, rows


# ---------------------------------------------------------------------------
# Step 2 — Generate LinkedIn post via Anthropic Claude
# ---------------------------------------------------------------------------

def generate_linkedin_post(topic: str, notes: str = "") -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    notes_section = f"\nExtra context / notes: {notes}" if notes.strip() else ""

    prompt = (
        f"{VOICE_PROFILE}\n\n"
        f"Write a LinkedIn post about this topic:\n"
        f"\"{topic}\"{notes_section}\n\n"
        "Write the post now. Do not include a subject line or title. Start directly with the opening sentence."
    )

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()


# ---------------------------------------------------------------------------
# Step 3 — Generate image via kie.ai (Flux.1 Kontext)
# ---------------------------------------------------------------------------

def _kie_headers() -> dict:
    return {
        "Authorization": f"Bearer {KIE_API_KEY}",
        "Content-Type": "application/json",
    }


def generate_image(topic: str, post_text: str) -> Image.Image:
    """
    Submits an image generation task to kie.ai using Flux.1 Kontext Pro,
    polls until complete, and returns a PIL Image.
    Endpoint: POST https://api.kie.ai/api/v1/flux/kontext/generate
    """
    image_prompt = (
        f"Minimalist editorial photograph, strictly black and white, high contrast, "
        f"no text, no logos, professional LinkedIn visual. Concept: {topic}. "
        "Clean composition, dramatic lighting, abstract or conceptual style."
    )

    payload = {
        "model": "flux-kontext-pro",
        "prompt": image_prompt,
        "aspectRatio": "1:1",
        "outputFormat": "png",
    }

    resp = requests.post(
        "https://api.kie.ai/api/v1/flux/kontext/generate",
        headers=_kie_headers(),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    # Response: {"code": 200, "msg": "success", "data": {"taskId": "..."}}
    task_id = (
        (data.get("data") or {}).get("taskId")
        or data.get("taskId")
        or data.get("task_id")
        or data.get("id")
    )
    if not task_id:
        raise RuntimeError(f"kie.ai returned no taskId: {data}")

    return _poll_image_task(task_id)


def _poll_image_task(task_id: str, max_attempts: int = 60, wait: int = 5) -> Image.Image:
    """
    Poll GET https://api.kie.ai/api/v1/flux/kontext/record-info?taskId={taskId}
    successFlag: 0=generating, 1=success, 2=create failed, 3=generation failed
    """
    for attempt in range(1, max_attempts + 1):
        resp = requests.get(
            "https://api.kie.ai/api/v1/flux/kontext/record-info",
            headers=_kie_headers(),
            params={"taskId": task_id},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        record = (data.get("data") or {})
        flag = record.get("successFlag")
        logger.info("Image generation — task %s successFlag=%s (attempt %d/%d)",
                    task_id, flag, attempt, max_attempts)

        if flag == 1:
            # Extract image URL from response
            url = (
                (record.get("response") or {}).get("resultImageUrl")
                or record.get("imageUrl")
                or record.get("url")
                or record.get("outputImageUrl")
                or (record.get("images") or [{}])[0].get("url")
            )
            if not url:
                raise RuntimeError(f"Task complete but no image URL found: {data}")
            return _download_image(url)

        if flag in (2, 3):
            raise RuntimeError(f"kie.ai image generation failed (flag={flag}): {data}")

        time.sleep(wait)

    raise TimeoutError(f"kie.ai image task {task_id} did not complete after {max_attempts} attempts.")


def _download_image(url: str) -> Image.Image:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGBA")


# ---------------------------------------------------------------------------
# Step 4 — Overlay logo with Pillow
# ---------------------------------------------------------------------------

def overlay_logo(base_image: Image.Image) -> Image.Image:
    """
    Pastes logo.png across the full bottom edge of base_image.
    The logo must have transparency (RGBA). It is scaled to the full
    width of the base image and anchored to the bottom edge.
    """
    if not os.path.exists(LOGO_PATH):
        logger.warning("logo.png not found — skipping logo overlay.")
        return base_image

    logo = Image.open(LOGO_PATH).convert("RGBA")

    # Scale logo so its width matches the base image exactly
    target_width = base_image.width
    scale = target_width / logo.width
    target_height = int(logo.height * scale)
    logo = logo.resize((target_width, target_height), Image.LANCZOS)

    # Anchor to bottom-left (edge to edge horizontally)
    x = 0
    y = base_image.height - target_height

    composite = base_image.copy()
    composite.paste(logo, (x, y), mask=logo)  # use logo's alpha channel as mask
    return composite


def save_image(image: Image.Image, path: str = TEMP_IMAGE) -> str:
    image.convert("RGB").save(path, "PNG")
    return path


# ---------------------------------------------------------------------------
# Step 5 — Post to LinkedIn
# ---------------------------------------------------------------------------

def _linkedin_headers(extra: dict = None) -> dict:
    h = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if extra:
        h.update(extra)
    return h


def _get_linkedin_person_urn() -> str:
    resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers=_linkedin_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    sub = resp.json().get("sub")
    if not sub:
        raise RuntimeError("Could not retrieve LinkedIn user 'sub' field.")
    return f"urn:li:person:{sub}"


def _register_image_upload(person_urn: str) -> tuple[str, str]:
    """Returns (upload_url, asset_urn)."""
    payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": person_urn,
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }
    resp = requests.post(
        "https://api.linkedin.com/v2/assets?action=registerUpload",
        headers=_linkedin_headers({"Content-Type": "application/json"}),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    value = resp.json()["value"]
    upload_url = value["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]["uploadUrl"]
    asset_urn = value["asset"]
    return upload_url, asset_urn


def _upload_image_bytes(upload_url: str, image_path: str) -> None:
    with open(image_path, "rb") as fh:
        resp = requests.put(
            upload_url,
            headers={
                "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
                "Content-Type": "image/png",
            },
            data=fh,
            timeout=120,
        )
    resp.raise_for_status()


def post_to_linkedin(post_text: str, image_path: str) -> str:
    """Returns the LinkedIn post URL."""
    logger.info("Fetching LinkedIn person URN...")
    person_urn = _get_linkedin_person_urn()

    logger.info("Registering image upload slot...")
    upload_url, asset_urn = _register_image_upload(person_urn)

    logger.info("Uploading image to LinkedIn...")
    _upload_image_bytes(upload_url, image_path)

    logger.info("Creating LinkedIn UGC post...")
    ugc_payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": post_text},
                "shareMediaCategory": "IMAGE",
                "media": [
                    {
                        "status": "READY",
                        "description": {"text": post_text[:200]},
                        "media": asset_urn,
                        "title": {"text": "Gradual Holdings"},
                    }
                ],
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    resp = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers=_linkedin_headers({"Content-Type": "application/json"}),
        json=ugc_payload,
        timeout=30,
    )
    resp.raise_for_status()

    post_id = resp.headers.get("x-restli-id", "unknown")
    return f"https://www.linkedin.com/feed/update/{post_id}/"


# ---------------------------------------------------------------------------
# Step 6 — Update topics.csv + write log.csv
# ---------------------------------------------------------------------------

def mark_topic_done(row_index: int, all_rows: list) -> None:
    all_rows[row_index]["status"]    = "done"
    all_rows[row_index]["post_date"] = datetime.now(TORONTO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

    fieldnames = ["topic", "notes", "status", "post_date"]
    with open(TOPICS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)


def append_to_log(topic: str, post_url: str) -> None:
    log_exists = os.path.isfile(LOG_CSV) and os.path.getsize(LOG_CSV) > 0
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "topic", "post_url"])
        if not log_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(TORONTO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "topic":     topic,
            "post_url":  post_url,
        })


# ---------------------------------------------------------------------------
# Main pipeline orchestrator
# ---------------------------------------------------------------------------

def run_pipeline() -> None:
    logger.info("=" * 60)
    logger.info("Pipeline run started")
    logger.info("=" * 60)

    # 1. Pick topic
    row_index, row, all_rows = read_next_queued_topic()
    if row is None:
        logger.info("No queued topics remaining. Nothing to post.")
        return

    topic = row["topic"].strip()
    notes = row.get("notes", "").strip()
    logger.info("Topic: %s", topic)

    # 2. Generate post text
    logger.info("Generating LinkedIn post via Claude...")
    post_text = generate_linkedin_post(topic, notes)
    logger.info("Post generated (%d chars):\n%s", len(post_text), post_text[:300] + "...")

    # 3. Generate image
    logger.info("Requesting image from kie.ai (Flux.1 Kontext)...")
    base_image = generate_image(topic, post_text)
    logger.info("Image received: %dx%d", base_image.width, base_image.height)

    # 4. Overlay logo
    logger.info("Overlaying logo...")
    final_image = overlay_logo(base_image)

    # 5. Save temp image
    image_path = save_image(final_image, TEMP_IMAGE)
    logger.info("Image saved to %s", image_path)

    # 6. Post to LinkedIn
    post_url = post_to_linkedin(post_text, image_path)
    logger.info("Posted to LinkedIn: %s", post_url)

    # 7. Update CSV + log
    mark_topic_done(row_index, all_rows)
    append_to_log(topic, post_url)
    logger.info("topics.csv updated. log.csv entry written.")

    # 8. Cleanup
    if os.path.exists(image_path):
        os.remove(image_path)

    logger.info("Pipeline run complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--run-now" in sys.argv:
        # One-shot execution — useful for testing or manual posts
        run_pipeline()
    else:
        # Scheduler: every 2 days at 08:00 America/Toronto
        now = datetime.now(TORONTO_TZ)
        next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if next_8am <= now:
            next_8am += timedelta(days=1)

        scheduler = BlockingScheduler(timezone=TORONTO_TZ)
        scheduler.add_job(
            run_pipeline,
            trigger=IntervalTrigger(days=2, start_date=next_8am, timezone=TORONTO_TZ),
            id="linkedin_post",
            name="LinkedIn post every 2 days at 08:00 Toronto",
            misfire_grace_time=3600,  # allow up to 1-hour late start
        )

        logger.info("Scheduler armed.")
        logger.info("Next run: %s", next_8am.strftime("%Y-%m-%d %H:%M:%S %Z"))
        logger.info("Interval: every 2 days. Press Ctrl+C to stop.")

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")
