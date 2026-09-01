"""Ask My Cohort backend: proxies the static frontend in ../web to Databricks.

Deploy target: Databricks Apps, with user authorization enabled for the `sql` and
`dashboards.genie` scopes (see app.yaml / the deployment notes handed to Ojash).
Databricks then forwards the signed-in user's own access token to every request
via the `x-forwarded-access-token` header — that token, not a shared service
account, is what's used for every SQL query and Genie call below. That's what
makes the Unity Catalog row filters (advisor_scope / dept_scope / student_self)
and the student_name column mask apply correctly per role: Genie and the SQL
warehouse both evaluate access using whichever identity made the call.

Local/dev mode: if DATABRICKS_TOKEN is set (a personal access token) and the
x-forwarded-access-token header is absent, that PAT is used instead. This is for
testing the API surface only — a PAT acts as its owner, so every visitor sees
that one person's rows. Never point a real multi-role demo at a PAT.
"""

import logging
import os
from pathlib import Path

from databricks.sdk import WorkspaceClient
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("uvicorn.error")

from genie import ask_genie
from queries import fetch_attendance_self, fetch_cohort, fetch_me, fetch_overview

DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST")
SQL_WAREHOUSE_ID = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID")
GENIE_SPACE_ID = os.environ.get("DATABRICKS_GENIE_SPACE_ID")
DEV_TOKEN = os.environ.get("DATABRICKS_TOKEN")  # local-only fallback, see module docstring

# Local dev: backend/ and web/ are siblings under the repo root (../web).
# Databricks Apps deployment: only backend/'s contents get synced to the app's
# workspace source path, with web/ synced alongside as a "web" subfolder of that
# same path (see deployment notes) — so fall back to ./web when ../web is absent.
_HERE = Path(__file__).resolve().parent
WEB_DIR = _HERE.parent / "web"
if not WEB_DIR.exists():
    WEB_DIR = _HERE / "web"

app = FastAPI(title="Ask My Cohort API")


def user_token(request: Request) -> str:
    token = request.headers.get("x-forwarded-access-token")
    if token:
        return token
    if DEV_TOKEN:
        return DEV_TOKEN
    raise HTTPException(
        status_code=401,
        detail=(
            "No user identity on this request. Deploy as a Databricks App with "
            "user_api_scopes [sql, dashboards.genie] enabled, or set DATABRICKS_TOKEN "
            "for local single-user testing."
        ),
    )


def require_sql_config() -> None:
    missing = [n for n, v in [("DATABRICKS_HOST", DATABRICKS_HOST), ("DATABRICKS_SQL_WAREHOUSE_ID", SQL_WAREHOUSE_ID)] if not v]
    if missing:
        raise HTTPException(status_code=501, detail=f"Backend not configured: missing {', '.join(missing)}")


def workspace_client(request: Request) -> WorkspaceClient:
    require_sql_config()
    # auth_type="pat" is required here: Databricks Apps also injects its own
    # OAuth service-principal credentials (DATABRICKS_CLIENT_ID/SECRET) into the
    # environment for the app's own identity. Without pinning auth_type, the SDK
    # sees both that ambient OAuth config and this explicit user token and
    # refuses to pick one ("more than one authorization method configured").
    return WorkspaceClient(host=DATABRICKS_HOST, token=user_token(request), auth_type="pat")


@app.get("/api/me")
def me(request: Request):
    """Who is asking, and what role does campus.ops.role_map give them.

    This is what makes the site identity-first: the frontend renders a student's,
    advisor's, dean's or admin's dashboard off this, instead of offering role tabs
    that anyone can click. A null role is a legitimate answer, not an error — it
    means the account has no role_map row, so every gold query returns zero rows.

    Under the DATABRICKS_TOKEN dev fallback current_user() collapses to the token
    owner, so this reports that identity's role rather than the browsing user's.
    """
    client = workspace_client(request)
    try:
        return fetch_me(client, SQL_WAREHOUSE_ID)
    except Exception as e:
        logger.exception("me failed")
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/gold/risk-signals")
def risk_signals(request: Request):
    client = workspace_client(request)
    try:
        return fetch_cohort(client, SQL_WAREHOUSE_ID)
    except Exception as e:
        logger.exception("risk_signals failed")
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/gold/attendance-buffers/me")
def attendance_self(request: Request):
    client = workspace_client(request)
    try:
        row = fetch_attendance_self(client, SQL_WAREHOUSE_ID)
    except Exception as e:
        logger.exception("attendance_self failed")
        raise HTTPException(status_code=502, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="No attendance_buffers row visible for this user.")
    return row


@app.get("/api/gold/overview")
def overview(request: Request):
    client = workspace_client(request)
    try:
        return fetch_overview(client, SQL_WAREHOUSE_ID)
    except Exception as e:
        logger.exception("overview failed")
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/genie/ask")
async def genie_ask(request: Request):
    if not GENIE_SPACE_ID or not DATABRICKS_HOST:
        raise HTTPException(status_code=501, detail="Backend not configured: missing DATABRICKS_HOST or DATABRICKS_GENIE_SPACE_ID")
    body = await request.json()
    question = (body or {}).get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    # Present on every message after the first, so Genie treats the exchange as one
    # conversation and follow-up questions resolve against earlier context.
    conversation_id = (body or {}).get("conversation_id") or None
    token = user_token(request)
    try:
        return ask_genie(DATABRICKS_HOST, token, GENIE_SPACE_ID, question, conversation_id)
    except Exception as e:
        logger.exception("genie ask failed")
        raise HTTPException(status_code=502, detail=f"Genie request failed: {e}")


# Mounted last: everything not matched by an /api/* route above falls through to
# the static frontend (index.html, app.js, styles.css, api.js) in ../web.
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
