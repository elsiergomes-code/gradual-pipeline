# gradual_pipeline

Automated LinkedIn content pipeline for **Elsie Gomes / Gradual Holdings Inc.**

Every 2 days at 08:00 Toronto time the pipeline:

1. Picks the next `queued` topic from `topics.csv`
2. Calls **Claude Sonnet 4.5** to write a 300-500 word LinkedIn post in Elsie's voice
3. Calls **kie.ai (Flux.1 Kontext)** to generate a minimalist black-and-white editorial image
4. Overlays `logo.png` edge-to-edge across the bottom of the image using Pillow
5. Uploads the image and posts text + image to your **LinkedIn personal profile**
6. Marks the topic as `done` in `topics.csv` and appends a row to `log.csv`

---

## Project structure

```
gradual_pipeline/
├── main.py            # Pipeline orchestrator + APScheduler
├── auth_server.py     # One-time LinkedIn OAuth 2.0 flow
├── topics.csv         # 20 pre-loaded topics (status: queued)
├── log.csv            # Appended after every successful post
├── logo.png           # YOUR logo file — place here before running
├── .env               # API keys and tokens (never commit this)
├── requirements.txt
└── README.md
```

---

## Prerequisites

- Python 3.10+
- A `logo.png` file with transparency (RGBA PNG) placed in this directory
- API accounts / credentials for:
  - [Anthropic](https://console.anthropic.com) — `ANTHROPIC_API_KEY`
  - [kie.ai](https://kie.ai) — `KIE_API_KEY`
  - [LinkedIn Developer App](https://www.linkedin.com/developers/apps) — `LINKEDIN_CLIENT_ID` + `LINKEDIN_CLIENT_SECRET`

---

## Installation

```bash
# 1. Create and activate a virtual environment
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt
```

---

## Configuration

Open `.env` and fill in every placeholder:

```env
ANTHROPIC_API_KEY=sk-ant-...
KIE_API_KEY=your_kie_key_here
LINKEDIN_CLIENT_ID=86abc...
LINKEDIN_CLIENT_SECRET=XYZ...
LINKEDIN_ACCESS_TOKEN=           # Leave blank — filled by auth_server.py
```

### Getting your LinkedIn API credentials

1. Go to [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) and create a new app.
2. Under **Auth**, add `http://localhost:8080/callback` as an **Authorized redirect URL**.
3. Under **Products**, request access to:
   - **Sign In with LinkedIn using OpenID Connect** (gives `openid profile`)
   - **Share on LinkedIn** (gives `w_member_social`)
4. Copy **Client ID** and **Client Secret** into `.env`.

---

## One-time LinkedIn OAuth setup

You must run this **once** to generate your access token. After that the token is saved in `.env` and reused automatically.

> LinkedIn access tokens typically last **60 days**. When yours expires, run this step again.

```bash
python auth_server.py
```

- A browser window opens automatically at `http://localhost:8080`
- Log in with your **personal LinkedIn account** (the profile you want to post from)
- Approve the permissions
- The server shows a success page and saves `LINKEDIN_ACCESS_TOKEN` to `.env`
- The server shuts itself down

---

## Running the pipeline

### Manual test run (one post, then exits)

```bash
python main.py --run-now
```

Use this to verify everything works before starting the scheduler.

### Start the scheduler (runs indefinitely)

```bash
python main.py
```

The scheduler will:
- Wait until the **next 08:00 Toronto time** (today's or tomorrow's, whichever is in the future)
- Then post once, and repeat every **2 days** at the same hour

Keep this terminal session alive (or run it in the background / as a service — see below).

---

## Running as a background service

### Windows — Task Scheduler

1. Open **Task Scheduler** → Create Basic Task
2. Trigger: At startup (or a specific time)
3. Action: `python C:\path\to\gradual_pipeline\main.py`
4. Ensure the working directory is set to the project folder

### macOS/Linux — systemd or screen

```bash
# Simple background run with nohup
nohup python main.py > pipeline.log 2>&1 &
```

---

## How topics.csv works

| Column      | Description                                      |
|-------------|--------------------------------------------------|
| `topic`     | The core idea — passed directly to Claude        |
| `notes`     | Optional extra context / direction (can be empty)|
| `status`    | `queued` → will be posted; `done` → already posted |
| `post_date` | Filled automatically after posting               |

To add more topics, append rows with `status = queued`.

---

## How log.csv works

After every successful post a row is appended:

```
timestamp,topic,post_url
2025-06-01 08:03:17 EDT,AI is not a tool it is infrastructure,https://www.linkedin.com/feed/update/urn:li:...
```

---

## Logo overlay details

`logo.png` must be:
- **RGBA PNG** (transparency required — no white background)
- Any size; it is automatically scaled to the **full width** of the generated image and anchored to the **bottom edge**

---

## Voice profile (Claude prompt)

Every post is written in Elsie's voice:

- Founder building in public — raw, strategic, never corporate
- Storytelling first: opens with a real moment or bold statement
- Short punchy sentences mixed with longer ones
- Facts over buzzwords — never uses: game-changer, leverage synergies, disruption, in today's rapidly evolving landscape
- Ends with something that lands, not a call to action
- 1 topic hashtag + `#GradualHoldings` + `#HumanAI`
- 300–500 words

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `401 Unauthorized` from LinkedIn | Token expired — re-run `auth_server.py` |
| `403 Forbidden` from LinkedIn | Check your LinkedIn app has `w_member_social` product enabled |
| kie.ai `401` | Check `KIE_API_KEY` in `.env` |
| kie.ai task times out | Increase `max_attempts` in `_poll_image_task()` in `main.py` |
| Logo not appearing | Ensure `logo.png` is RGBA (transparent background) and in the project root |
| `No queued topics` | All 20 topics have been posted — add new rows to `topics.csv` |
