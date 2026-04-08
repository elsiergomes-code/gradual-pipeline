"""
gradual_pipeline/auth_server.py

One-time LinkedIn OAuth 2.0 flow.

Run this ONCE to generate your LINKEDIN_ACCESS_TOKEN and save it to .env.
After that you never need to run it again unless your token expires (~60 days).

Usage:
  python auth_server.py

Then open http://localhost:8080 in your browser and follow the LinkedIn login prompt.
The access token is saved automatically to your .env file.
"""

import os
import secrets
import webbrowser
import threading

import requests
from flask import Flask, request, redirect
from dotenv import load_dotenv, set_key

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CLIENT_ID     = os.getenv("LINKEDIN_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
REDIRECT_URI  = "http://localhost:8080/callback"
PORT          = 8080

# LinkedIn scopes needed for reading profile + posting content
# openid + profile  → user identity (sub, name)
# w_member_social   → create UGC posts / shares
SCOPE = "openid profile w_member_social"

# CSRF protection — generate a fresh state token each run
OAUTH_STATE = secrets.token_urlsafe(24)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route("/")
def index():
    """Redirect the browser straight to LinkedIn's authorisation URL."""
    if not CLIENT_ID:
        return (
            "<h2>Error</h2>"
            "<p>LINKEDIN_CLIENT_ID is not set in your .env file.</p>"
            "<p>Fill it in and restart the server.</p>",
            500,
        )

    auth_url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={SCOPE}"
        f"&state={OAUTH_STATE}"
    )
    return redirect(auth_url)


@app.route("/callback")
def callback():
    """LinkedIn redirects here with ?code=... after the user approves."""
    error = request.args.get("error")
    if error:
        description = request.args.get("error_description", "")
        return f"<h2>OAuth Error</h2><p>{error}: {description}</p>", 400

    returned_state = request.args.get("state", "")
    if returned_state != OAUTH_STATE:
        return "<h2>State mismatch</h2><p>Possible CSRF attempt — please retry.</p>", 400

    code = request.args.get("code")
    if not code:
        return "<h2>No code returned by LinkedIn.</h2>", 400

    # Exchange authorisation code for access token
    token_resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )

    if token_resp.status_code != 200:
        return (
            f"<h2>Token exchange failed</h2>"
            f"<pre>{token_resp.status_code}: {token_resp.text}</pre>",
            400,
        )

    token_data    = token_resp.json()
    access_token  = token_data.get("access_token")
    expires_in    = token_data.get("expires_in", "unknown")

    if not access_token:
        return f"<h2>No access_token in response</h2><pre>{token_data}</pre>", 400

    # Persist token to .env
    set_key(".env", "LINKEDIN_ACCESS_TOKEN", access_token)

    # Verify it works by fetching basic profile info
    profile_resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    profile_name = "unknown"
    if profile_resp.ok:
        profile = profile_resp.json()
        profile_name = profile.get("name") or profile.get("sub", "unknown")

    # Shut server down after success (non-blocking)
    shutdown = request.environ.get("werkzeug.server.shutdown")
    if shutdown:
        threading.Timer(1.5, shutdown).start()
    else:
        # Flask >= 2.x: signal shutdown via OS
        threading.Timer(1.5, lambda: os.kill(os.getpid(), 2)).start()

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>Authenticated</title></head>
    <body style="font-family:sans-serif;max-width:600px;margin:60px auto;padding:0 20px">
        <h2 style="color:#0a66c2">LinkedIn Authentication Successful</h2>
        <p>Logged in as: <strong>{profile_name}</strong></p>
        <p>Token expires in: <strong>{expires_in} seconds</strong>
           (~{int(expires_in) // 86400 if str(expires_in).isdigit() else "?"} days)</p>
        <p>Your <code>LINKEDIN_ACCESS_TOKEN</code> has been saved to <code>.env</code>.</p>
        <p style="color:#555">You can close this window. The server is shutting down.</p>
        <hr>
        <p>Next step: run the pipeline<br>
        <code>python main.py --run-now</code></p>
    </body>
    </html>
    """


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not CLIENT_ID or not CLIENT_SECRET:
        print(
            "\n[ERROR] LINKEDIN_CLIENT_ID and/or LINKEDIN_CLIENT_SECRET are not set.\n"
            "Edit .env and fill in both values before running this script.\n"
        )
        raise SystemExit(1)

    url = f"http://localhost:{PORT}"
    print(f"\n{'='*55}")
    print("  LinkedIn OAuth — one-time setup")
    print(f"{'='*55}")
    print(f"\n  Server listening on {url}")
    print("  Opening browser automatically...")
    print("\n  If the browser does not open, visit:")
    print(f"  {url}\n")

    # Try to open the browser automatically after a short delay
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app.run(host="0.0.0.0", port=PORT, debug=False)
