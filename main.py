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
import json
import time
import random
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

ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
KIE_API_KEY           = os.getenv("KIE_API_KEY", "")
LINKEDIN_ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN", "")

TOPICS_CSV       = "topics.csv"
LOG_CSV          = "log.csv"
LOGO_PATH        = "logo.png"
TEMP_IMAGE       = "temp_post_image.png"
SYSTEM_PROMPT_FILE = "system_prompt.txt"
SEEDS_FILE       = "seeds.json"

TORONTO_TZ = pytz.timezone("America/Toronto")


# ---------------------------------------------------------------------------
# Layer 1 — Load persona system prompt (once at startup)
# ---------------------------------------------------------------------------

def load_system_prompt() -> str:
    if not os.path.exists(SYSTEM_PROMPT_FILE):
        logger.warning("%s not found — using fallback voice.", SYSTEM_PROMPT_FILE)
        return (
            "You are Elsie Gomes, founder of Gradual Holdings Inc. "
            "Write in a direct, warm, founder-building-in-public voice. "
            "No corporate language. Short punchy sentences. End with something that lands."
        )
    with open(SYSTEM_PROMPT_FILE, encoding="utf-8") as f:
        return f.read().strip()


# ---------------------------------------------------------------------------
# Layer 3 — Load story seeds (once at startup)
# ---------------------------------------------------------------------------

def load_seeds() -> list:
    if not os.path.exists(SEEDS_FILE):
        logger.warning("%s not found — posts will run without story seeds.", SEEDS_FILE)
        return []
    with open(SEEDS_FILE, encoding="utf-8") as f:
        return json.load(f)


def pick_seed(seeds: list, pillar: str) -> dict | None:
    """Return a seed matching the pillar, falling back to any random seed."""
    if not seeds:
        return None
    matching = [s for s in seeds if s.get("pillar", "").lower() == pillar.lower()]
    if matching:
        return random.choice(matching)
    return random.choice(seeds)


# ---------------------------------------------------------------------------
# Step 1 — Read next queued topic
# ---------------------------------------------------------------------------

def read_next_queued_topic():
    """Return (row_index, row_dict, all_rows) for the first queued row."""
    with open(TOPICS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows):
        if row["status"].strip().lower() == "queued":
            return i, row, rows

    return None, None, rows


# ---------------------------------------------------------------------------
# Step 2 — Generate LinkedIn post via Anthropic Claude
# ---------------------------------------------------------------------------

def build_user_prompt(topic: str, angle: str, notes: str, seed: dict | None) -> str:
    """
    Assembles the user-turn prompt from the topic row + optional story seed.
    The system prompt (persona/voice) is passed separately to the API.
    """
    parts = []

    parts.append(f"Topic: {topic}")

    if angle.strip():
        parts.append(f"Post angle: {angle}")

    if notes.strip():
        parts.append(f"Extra context: {notes}")

    if seed:
        parts.append(
            f"\nReal story to draw from — use the texture and specific detail, "
            f"not necessarily the exact words:\n\"{seed['hook']}\""
        )
        parts.append(
            "Weave this story in naturally if it fits. Don't force it if the topic "
            "pulls in a different direction."
        )

    parts.append(
        "\nWrite the LinkedIn post now. "
        "Do not include a subject line or title. "
        "Start directly with the opening sentence."
    )

    return "\n".join(parts)


def generate_linkedin_post(
    topic: str,
    angle: str,
    notes: str,
    system_prompt: str,
    seed: dict | None,
) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    user_prompt = build_user_prompt(topic, angle, notes, seed)

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
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
    for attempt in range(1, max_attempts + 1):
        resp = requests.get(
            "https://api.kie.ai/api/v1/flux/kontext/record-info",
            headers=_kie_headers(),
            params={"taskId": task_id},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        record = data.get("data") or {}
        flag = record.get("successFlag")
        logger.info("Image generation — task %s successFlag=%s (attempt %d/%d)",
                    task_id, flag, attempt, max_attempts)

        if flag == 1:
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
    if not os.path.exists(LOGO_PATH):
        logger.warning("logo.png not found — skipping logo overlay.")
        return base_image

    logo = Image.open(LOGO_PATH).convert("RGBA")

    target_width = base_image.width
    scale = target_width / logo.width
    target_height = int(logo.height * scale)
    logo = logo.resize((target_width, target_height), Image.LANCZOS)

    x = 0
    y = base_image.height - target_height

    composite = base_image.copy()
    composite.paste(logo, (x, y), mask=logo)
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

    fieldnames = ["topic", "pillar", "angle", "notes", "status", "post_date"]
    with open(TOPICS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)


def append_to_log(topic: str, pillar: str, seed_id: str, post_url: str) -> None:
    log_exists = os.path.isfile(LOG_CSV) and os.path.getsize(LOG_CSV) > 0
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["timestamp", "topic", "pillar", "seed_used", "post_url"]
        )
        if not log_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(TORONTO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "topic":     topic,
            "pillar":    pillar,
            "seed_used": seed_id,
            "post_url":  post_url,
        })


# ---------------------------------------------------------------------------
# Main pipeline orchestrator
# ---------------------------------------------------------------------------

# Load persona + seeds once — shared across all scheduler runs
SYSTEM_PROMPT = load_system_prompt()
SEEDS         = load_seeds()

logger.info("System prompt loaded (%d chars).", len(SYSTEM_PROMPT))
logger.info("Seeds loaded: %d entries.", len(SEEDS))


def run_pipeline() -> None:
    logger.info("=" * 60)
    logger.info("Pipeline run started")
    logger.info("=" * 60)

    # 1. Pick topic
    row_index, row, all_rows = read_next_queued_topic()
    if row is None:
        logger.info("No queued topics remaining. Nothing to post.")
        return

    topic  = row["topic"].strip()
    pillar = row.get("pillar", "").strip()
    angle  = row.get("angle", "").strip()
    notes  = row.get("notes", "").strip()
    logger.info("Topic : %s", topic)
    logger.info("Pillar: %s | Angle: %s", pillar or "—", angle or "—")

    # 2. Pick a story seed
    seed = pick_seed(SEEDS, pillar)
    if seed:
        logger.info("Seed selected: %s", seed["id"])
    else:
        logger.info("No seed selected.")

    # 3. Generate post text
    logger.info("Generating LinkedIn post via Claude...")
    post_text = generate_linkedin_post(topic, angle, notes, SYSTEM_PROMPT, seed)
    logger.info("Post generated (%d chars):\n%s", len(post_text), post_text[:300] + "...")

    # 4. Generate image
    logger.info("Requesting image from kie.ai (Flux.1 Kontext)...")
    base_image = generate_image(topic, post_text)
    logger.info("Image received: %dx%d", base_image.width, base_image.height)

    # 5. Overlay logo
    logger.info("Overlaying logo...")
    final_image = overlay_logo(base_image)

    # 6. Save temp image
    image_path = save_image(final_image, TEMP_IMAGE)
    logger.info("Image saved to %s", image_path)

    # 7. Post to LinkedIn
    post_url = post_to_linkedin(post_text, image_path)
    logger.info("Posted to LinkedIn: %s", post_url)

    # 8. Update CSV + log
    seed_id = seed["id"] if seed else "none"
    mark_topic_done(row_index, all_rows)
    append_to_log(topic, pillar, seed_id, post_url)
    logger.info("topics.csv updated. log.csv entry written.")

    # 9. Cleanup
    if os.path.exists(image_path):
        os.remove(image_path)

    logger.info("Pipeline run complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--run-now" in sys.argv:
        run_pipeline()
    else:
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
            misfire_grace_time=3600,
        )

        logger.info("Scheduler armed.")
        logger.info("Next run: %s", next_8am.strftime("%Y-%m-%d %H:%M:%S %Z"))
        logger.info("Interval: every 2 days. Press Ctrl+C to stop.")

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")