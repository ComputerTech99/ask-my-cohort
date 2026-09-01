"""Ask My Cohort's "Ask" box, wired to the Databricks Genie Conversations API.

Each call starts a fresh Genie conversation — the frontend doesn't carry a
conversation_id between questions yet, so there's no multi-turn follow-up.
That's a deliberate v1 simplification, not a platform limit: the Genie API
supports continuing a conversation via create_message_and_wait(space_id,
conversation_id, content) if that's worth adding later.
"""

from datetime import timedelta

from databricks.sdk import WorkspaceClient

# start_conversation_and_wait defaults to a 20-minute timeout, which just leaves
# the browser's "Asking..." spinning with no feedback if Genie is ever slow.
# Fail fast instead so the frontend can show a clear error and let the user retry.
GENIE_TIMEOUT = timedelta(seconds=55)


def ask_genie(host: str, user_token: str, space_id: str, question: str) -> str:
    # auth_type="pat" pins the SDK to this explicit user token — see the matching
    # comment in app.py's workspace_client() for why that's required on Apps.
    client = WorkspaceClient(host=host, token=user_token, auth_type="pat")
    message = client.genie.start_conversation_and_wait(space_id=space_id, content=question, timeout=GENIE_TIMEOUT)

    for attachment in message.attachments or []:
        text = getattr(attachment, "text", None)
        content = getattr(text, "content", None) if text else None
        if content:
            return content

    return (
        "Genie answered but didn't return a text summary — it likely produced a result "
        "table instead. Open the Genie space directly to see the query result."
    )
