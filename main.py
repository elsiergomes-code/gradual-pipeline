"""
gradual_pipeline/main.py

LinkedIn automation pipeline for Elsie Gomes / Gradual Holdings Inc.
Reads topics.csv, generates a post + image, posts to LinkedIn, and schedules itself
to run every 2 days at 08:00 America/Toronto.

Algorithm strategy:
  - Three post types cycle in strict order: insight → document → tension → repeat
  - Each type targets a different LinkedIn signal:
      insight  → shares + reach (sharp truth, no fluff)
      document → saves + trust (specific real build moment)
      tension  → comments (unresolved thing, implicit "has this happened to you?")
  - Posts stay in one content cluster: AI systems + documenting the real-time build
  - Each post feels like an episode — reader senses they walked in on something
    already in motion and wants to follow the thread

Usage:
  python main.py            # Start the APScheduler (runs on a 2-day interval)
  python main.py --run-now  # Execute one pipeline run immediately (for testing)
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

TOPICS_CSV         = "topics.csv"
LOG_CSV            = "log.csv"
LOGO_PATH          = "logo.png"
TEMP_IMAGE         = "temp_post_image.png"
SYSTEM_PROMPT_FILE = "system_prompt.txt"
SEEDS_FILE         = "seeds.json"

TORONTO_TZ = pytz.timezone("America/Toronto")

# Strict cycle — never random
POST_TYPE_CYCLE = ["insight", "document", "tension"]


# ---------------------------------------------------------------------------
# Post type prompt instructions
# Each targets a specific LinkedIn algorithm signal + comment psychology
# ---------------------------------------------------------------------------

POST_TYPE_INSTRUCTIONS = {

    "insight": """
POST TYPE: Insight — engineered for SHARES and REACH.

Goal: write something so precise the reader stops scrolling and thinks
"I've never seen it put that way but that's exactly right."
They share it because sharing it makes them look like someone who sees clearly.

Structure:
- Open with a single sharp statement. No preamble. No "I've been thinking about..."
  Just the truth, stated plainly. Slightly uncomfortable — the kind of thing
  most people sense but haven't said out loud yet.
- Middle: 2-3 short paragraphs building the case with specific observations.
  No theory. No framework names. Just what you actually see happening.
- End with one line that lands like a door closing. Not a question.
  A statement that leaves a small echo. Something the reader will still be
  thinking about in an hour.

Comment trigger: reader types "this" or "exactly" or shares their own version
of the same observation. The post earns the comment by being so precise
that silence feels wrong.

NEVER end with a question. NEVER say "drop a comment." The post does the work.
""".strip(),

    "document": """
POST TYPE: Document — engineered for SAVES and TRUST.

Goal: show the actual work. Not the cleaned-up lesson — the thing happening
right now, with specific details that prove you were really there doing it.

Structure:
- Open with one concrete moment. Something specific that happened.
  Not "I've been building X" — more like "Yesterday I hit a wall with X
  and here is exactly what I found."
- Middle: walk through what you did, what broke, what you tried, what worked.
  Name the actual tools (Make.com, Railway, Lovable, Claude, kie.ai, Framer).
  Name actual numbers if you have them. Specificity is trust.
- End with where the build is right now — not the lesson, not the takeaway.
  Just: this is where it is today. Leave it open. The reader should feel
  like they can check back in two days and get the next episode.

Comment trigger: "I had the exact same issue with X" or "which tool did you
use for Y?" — the specific detail you named just solved or named something
they were stuck on.
""".strip(),

    "tension": """
POST TYPE: Tension — engineered for COMMENTS.

This is the most important post type for the algorithm. Comments are the signal
LinkedIn weights most. One real comment thread can 10x distribution.

Goal: write something unresolved. Not a problem you've solved — a tension
you are actually sitting with. Something with two valid sides and no clean answer.
Something where the reader has an opinion and needs to share it because
you haven't resolved it for them.

Structure:
- Open by naming the tension directly. Two things that are both true and
  pulling in opposite directions. State them plainly, no decoration.
- Middle: give each side its best argument. Don't pick one. Make both
  sides feel real and valid. The reader's job is to pick one.
- End with the line you are actually sitting with right now. Not "what do
  you think?" — something more like a statement of where you are that implies
  you genuinely don't know the answer yet. Their brain needs to resolve it.
  That is when they type.

Comment triggers: strong agreement, strong disagreement, or a breakthrough
in their own thinking — "oh wait, I've been thinking about this wrong."
All three drive comments. Post should feel like walking into a conversation
already in progress.

CRITICAL: do not resolve the tension. Do not end with a lesson or conclusion.
End with the unresolved thing, stated honestly. Silence after reading this
post should feel impossible.
""".strip(),

}


# Image visual language per post type
IMAGE_STYLE = {
    "insight": (
        "Minimalist editorial photograph, strictly black and white, extreme high contrast, "
        "dramatic single light source, abstract geometric composition, no text, no logos, "
        "the visual feeling of sudden clarity. Professional LinkedIn visual."
    ),
    "document": (
        "Minimalist editorial photograph, strictly black and white, high contrast, "
        "sense of work in progress — tools, process, hands, screens, making something. "
        "Documentary feel. No text, no logos. Professional LinkedIn visual."
    ),
    "tension": (
        "Minimalist editorial photograph, strictly black and white, high contrast, "
        "two opposing forces in frame — light and shadow, open and closed, motion and stillness. "
        "Visual feeling of an unresolved question. No text, no logos. Professional LinkedIn visual."
    ),
}


# ---------------------------------------------------------------------------
# Layer 1 — Load persona system prompt
# ---------------------------------------------------------------------------

def load_system_prompt() -> str:
    if not os.path.exists(SYSTEM_PROMPT_FILE):
        logger.warning("%s not found — using fallback voice.", SYSTEM_PROMPT_FILE)
        return (
            "You are Elsie Gomes, founder of Gradual Holdings Inc. "
            "Write in a direct, warm, founder-building-in-public voice. "
            "No corporate language. Short punchy sentences."
        )
    with open(SYSTEM_PROMPT_FILE, encoding="utf-8") as f:
        return f.read().strip()


# ---------------------------------------------------------------------------
# Layer 3 — Load story seeds
# ---------------------------------------------------------------------------

def load_seeds() -> list:
    if not os.path.exists(SEEDS_FILE):
        logger.warning("%s not found — posts will run without story seeds.", SEEDS_FILE)
        return []
    with open(SEEDS_FILE, encoding="utf-8") as f:
        return json.load(f)


def pick_seed(seeds: list, pillar: str, post_type: str) -> dict | None:
    """Match by pillar + post_type first, fall back to pillar, then any."""
    if not seeds:
        return None

    best = [s for s in seeds
            if s.get("pillar", "").lower() == pillar.lower()
            and s.get("post_type", "").lower() == post_type.lower()]
    if best:
        return random.choice(best)

    pillar_match = [s for s in seeds if s.get("pillar", "").lower() == pillar.lower()]
    if pillar_match:
        return random.choice(pillar_match)

    return random.choice(seeds)


# ---------------------------------------------------------------------------
# Step 1 — Read next queued topic + determine post type from cycle
# ---------------------------------------------------------------------------

def read_next_queued_topic():
    """
    Returns (row_index, row_dict, all_rows, post_type).
    Post type cycles insight → document → tension based on done count.
    CSV post_type column can override if explicitly set.
    """
    with open(TOPICS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    done_count = sum(1 for r in rows if r["status"].strip().lower() == "done")
    cycle_post_type = POST_TYPE_CYCLE[done_count % len(POST_TYPE_CYCLE)]

    for i, row in enumerate(rows):
        if row["status"].strip().lower() == "queued":
            explicit = row.get("post_type", "").strip().lower()
            post_type = explicit if explicit in POST_TYPE_CYCLE else cycle_post_type
            return i, row, rows, post_type

    return None, None, rows, cycle_post_type


# ---------------------------------------------------------------------------
# Step 2 — Generate LinkedIn post via Anthropic Claude
# ---------------------------------------------------------------------------

def build_user_prompt(
    topic: str,
    angle: str,
    notes: str,
    post_type: str,
    seed: dict | None,
) -> str:
    parts = []

    parts.append(POST_TYPE_INSTRUCTIONS[post_type])
    parts.append("")
    parts.append(f"Topic: {topic}")

    if angle.strip():
        parts.append(f"Angle: {angle}")

    if notes.strip():
        parts.append(f"Context: {notes}")

    if seed:
        parts.append(
            f"\nReal moment to ground this in — use the specific texture and detail, "
            f"not the exact words:\n\"{seed['hook']}\""
        )

    parts.append(
        "\nWrite the post now. No title. No subject line. "
        "Start with the first word. Hashtags on the final line only."
    )

    return "\n".join(parts)


def generate_linkedin_post(
    topic: str,
    angle: str,
    notes: str,
    post_type: str,
    system_prompt: str,
    seed: dict | None,
) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    user_prompt = build_user_prompt(topic, angle, notes, post_type, seed)

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


def generate_image(topic: str, post_text: str, post_type: str) -> Image.Image:
    style = IMAGE_STYLE.get(post_type, IMAGE_STYLE["insight"])
    image_prompt = f"{style} Concept: {topic}."

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
# Step 4 — Overlay logo
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

    composite = base_image.copy()
    composite.paste(logo, (0, base_image.height - target_height), mask=logo)
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

def mark_topic_done(row_index: int, all_rows: list, post_type: str) -> None:
    all_rows[row_index]["status"]    = "done"
    all_rows[row_index]["post_type"] = post_type
    all_rows[row_index]["post_date"] = datetime.now(TORONTO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

    fieldnames = ["topic", "pillar", "angle", "post_type", "notes", "status", "post_date"]
    with open(TOPICS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)


def append_to_log(topic: str, pillar: str, post_type: str, seed_id: str, post_url: str) -> None:
    log_exists = os.path.isfile(LOG_CSV) and os.path.getsize(LOG_CSV) > 0
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["timestamp", "topic", "pillar", "post_type", "seed_used", "post_url"]
        )
        if not log_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(TORONTO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
            "topic":     topic,
            "pillar":    pillar,
            "post_type": post_type,
            "seed_used": seed_id,
            "post_url":  post_url,
        })


# ---------------------------------------------------------------------------
# Main pipeline orchestrator
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = load_system_prompt()
SEEDS         = load_seeds()

logger.info("System prompt loaded (%d chars).", len(SYSTEM_PROMPT))
logger.info("Seeds loaded: %d entries.", len(SEEDS))


def run_pipeline() -> None:
    logger.info("=" * 60)
    logger.info("Pipeline run started")
    logger.info("=" * 60)

    # 1. Pick topic + post type
    row_index, row, all_rows, post_type = read_next_queued_topic()
    if row is None:
        logger.info("No queued topics remaining. Nothing to post.")
        return

    topic  = row["topic"].strip()
    pillar = row.get("pillar", "").strip()
    angle  = row.get("angle", "").strip()
    notes  = row.get("notes", "").strip()

    logger.info("Topic     : %s", topic)
    logger.info("Pillar    : %s", pillar or "—")
    logger.info("Angle     : %s", angle or "—")
    logger.info("Post type : %s", post_type.upper())

    # 2. Pick seed
    seed = pick_seed(SEEDS, pillar, post_type)
    if seed:
        logger.info("Seed      : %s (%s)", seed["id"], seed.get("post_type", "any"))
    else:
        logger.info("Seed      : none")

    # 3. Generate post
    logger.info("Generating post via Claude [type=%s]...", post_type)
    post_text = generate_linkedin_post(topic, angle, notes, post_type, SYSTEM_PROMPT, seed)
    logger.info("Post generated (%d chars):\n%s", len(post_text), post_text[:400] + "...")

    # 4. Generate image
    logger.info("Requesting image from kie.ai [style=%s]...", post_type)
    base_image = generate_image(topic, post_text, post_type)
    logger.info("Image received: %dx%d", base_image.width, base_image.height)

    # 5. Logo overlay
    final_image = overlay_logo(base_image)

    # 6. Save
    image_path = save_image(final_image, TEMP_IMAGE)
    logger.info("Image saved to %s", image_path)

    # 7. Post
    post_url = post_to_linkedin(post_text, image_path)
    logger.info("Posted: %s", post_url)

    # 8. Log
    seed_id = seed["id"] if seed else "none"
    mark_topic_done(row_index, all_rows, post_type)
    append_to_log(topic, pillar, post_type, seed_id, post_url)
    logger.info("CSV + log updated.")

    # 9. Cleanup
    if os.path.exists(image_path):
        os.remove(image_path)

    logger.info("Done. [%s]", post_type.upper())


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
        logger.info("Next run  : %s", next_8am.strftime("%Y-%m-%d %H:%M:%S %Z"))
        logger.info("Interval  : every 2 days")
        logger.info("Cycle     : insight → document → tension → repeat")
        logger.info("Press Ctrl+C to stop.")

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")