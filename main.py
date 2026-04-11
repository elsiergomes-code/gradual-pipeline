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

POST_TYPE_CYCLE = ["insight", "document", "tension"]


# ---------------------------------------------------------------------------
# Post type prompt instructions
# ---------------------------------------------------------------------------

POST_TYPE_INSTRUCTIONS = {

    "insight": """
POST TYPE: Insight — engineered for SHARES and REACH.

VOICE AND TONE — THIS IS THE MOST IMPORTANT INSTRUCTION:
Write as an encouraging, passionate thought leader who genuinely wants the reader
to understand and benefit from what they are reading. Not a lecturer. Not a
Silicon Valley insider. Someone who has lived through something and is sharing
what they actually learned in plain language that anyone can follow.

The reader may have zero background on AI, technology, or business. Write as if
you are explaining something to a smart friend who has never heard of this topic
before. Every concept that could be unfamiliar must be explained in one plain
sentence before moving on. The insight should feel like a door opening, not a
door closing.

Warmth is not weakness. Passion is not hype. You can be direct and encouraging
at the same time. The reader should feel smarter after reading this post, not
smaller.

Structure:
- Open with a single clear observation anyone can immediately understand.
  No jargon. No insider language. Just something true, stated plainly.
- Middle: 2-3 short paragraphs that build the idea with real examples.
  If you use a technical term, explain it immediately in plain words.
  Example: "AI agents — which are basically AI systems that can take actions
  on your behalf without you needing to be there — can now handle..."
- End with one encouraging line that makes the reader feel like they can
  act on this. Not a question. A statement that opens a door.

NEVER end with a question. NEVER use buzzwords without explaining them first.
NEVER make the reader feel like they need a computer science degree to understand.
The post earns shares by making people feel seen and informed, not impressed.
""".strip(),

    "document": """
POST TYPE: Document — engineered for SAVES and TRUST.

VOICE AND TONE — THIS IS THE MOST IMPORTANT INSTRUCTION:
Write as a founder documenting the real work in progress — warm, honest, and
specific. The reader does not need to know what Railway or APScheduler is before
reading this post. When you name a tool, explain what it does in one plain phrase.
Example: "Railway — the platform I use to keep the automation running in the
cloud — crashed because..."

The goal is for someone who has never built anything technical to follow along
and feel like they understand exactly what happened and why it matters. Specificity
builds trust. Plain language makes the specificity accessible.

Write like you are telling a friend what happened today — not writing a case study.

Structure:
- Open with one concrete moment. Something specific that happened.
  Ground it in reality immediately.
- Middle: walk through what you did, what broke, what you tried, what worked.
  Name the actual tools but always follow a tool name with what it does in
  plain language if there is any chance the reader does not know.
  Name actual numbers if you have them.
- End with where the build is right now. Leave it open like an episode ending.
  The reader should feel like they are following a story, not reading a tutorial.

Comment trigger: "I had the exact same issue" or "which tool did you use for Y?"
""".strip(),

    "tension": """
POST TYPE: Tension — engineered for COMMENTS.

VOICE AND TONE — THIS IS THE MOST IMPORTANT INSTRUCTION:
Write as someone genuinely sitting with an unresolved question — warm, honest,
and human. Not cold and philosophical. Not a LinkedIn thought experiment.
A real tension you are actually living with, shared in a way that makes the
reader feel like you are having a conversation with them, not presenting a thesis.

Explain the tension in plain language that anyone can follow. If the tension
involves technical or business concepts, set them up first so the reader
understands what is at stake before you get into the unresolved part.

The reader should feel like you genuinely do not know the answer — because you
do not — and that their opinion actually matters to you. That is what drives
real comments.

Structure:
- Open by naming the tension in plain everyday language. Two things that are
  both true and pulling in opposite directions. Anyone should understand both
  sides immediately.
- Middle: give each side its full argument. Make both sides feel real and valid.
  Write with warmth toward both sides — you are not debating, you are sharing.
- End with the honest line of where you are sitting right now. Not resolved.
  Not a call to action. Just the truth of where you are with it.

CRITICAL: do not resolve the tension. Do not end with a lesson.
End with the unresolved thing, stated with honesty and warmth.
""".strip(),

}


# ---------------------------------------------------------------------------
# Image style — STRICTLY NO PEOPLE, NO FACES, NO STOCK PHOTOS
# ---------------------------------------------------------------------------

IMAGE_STYLE = {
    "insight": (
        "Abstract digital art, dark navy background, glowing geometric light trails, "
        "data streams visualized as flowing lines of light, deep blues and electric teals, "
        "cinematic and minimal. "
        "STRICT RULES: absolutely no people, no faces, no human figures, no portraits, "
        "no hands, no bodies. Pure abstract visual only. No text. No logos."
    ),
    "document": (
        "Abstract digital art, dark background, visual representation of a system being built — "
        "interconnected nodes, circuit-like patterns, soft glowing connections between points, "
        "deep charcoal and warm amber tones, sense of things coming together. "
        "STRICT RULES: absolutely no people, no faces, no human figures, no portraits, "
        "no hands, no bodies. Pure abstract visual only. No text. No logos."
    ),
    "tension": (
        "Abstract digital art, split composition, two opposing visual forces — "
        "one side cool deep blue, one side warm amber, meeting at a sharp boundary in the center, "
        "minimal geometric forms, sense of balance and tension simultaneously. "
        "STRICT RULES: absolutely no people, no faces, no human figures, no portraits, "
        "no hands, no bodies. Pure abstract visual only. No text. No logos."
    ),
}


# ---------------------------------------------------------------------------
# Layer 1 — Load persona system prompt
# ---------------------------------------------------------------------------

def load_system_prompt() -> str:
    if not os.path.exists(SYSTEM_PROMPT_FILE):
        logger.warning("%s not found — using fallback voice.", SYSTEM_PROMPT_FILE)
        return (
            "You are Elsie Gomes, founder of Gradual Holdings Inc. and rawstate.ai. "
            "Write as an encouraging, passionate thought leader. "
            "Warm, direct, and accessible. Never use jargon without explaining it. "
            "Every reader should feel smarter after reading your post, not smaller."
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
        "\nCRITICAL REMINDER before you write: "
        "This post must be fully understandable by someone with zero background "
        "in AI, technology, or business. If you use any term that could be unfamiliar, "
        "explain it immediately in plain language. Write warmly. Write with encouragement. "
        "The reader should feel like a door just opened for them, not like they missed "
        "a class they should have taken. "
        "\n\nWrite the post now. No title. No subject line. "
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
    image_prompt = (
        f"{style} "
        f"The visual should abstractly represent this concept: {topic}. "
        f"Remember: no people, no faces, no human figures under any circumstances."
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

    seed = pick_seed(SEEDS, pillar, post_type)
    if seed:
        logger.info("Seed      : %s (%s)", seed["id"], seed.get("post_type", "any"))
    else:
        logger.info("Seed      : none")

    logger.info("Generating post via Claude [type=%s]...", post_type)
    post_text = generate_linkedin_post(topic, angle, notes, post_type, SYSTEM_PROMPT, seed)
    logger.info("Post generated (%d chars):\n%s", len(post_text), post_text[:400] + "...")

    logger.info("Requesting image from kie.ai [style=%s]...", post_type)
    base_image = generate_image(topic, post_text, post_type)
    logger.info("Image received: %dx%d", base_image.width, base_image.height)

    final_image = overlay_logo(base_image)
    image_path = save_image(final_image, TEMP_IMAGE)
    logger.info("Image saved to %s", image_path)

    post_url = post_to_linkedin(post_text, image_path)
    logger.info("Posted: %s", post_url)

    seed_id = seed["id"] if seed else "none"
    mark_topic_done(row_index, all_rows, post_type)
    append_to_log(topic, pillar, post_type, seed_id, post_url)
    logger.info("CSV + log updated.")

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