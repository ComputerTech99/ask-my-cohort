"""The Ask My Cohort agent, wired to the Databricks Genie Conversations API.

Conversations are stateful. The first question starts one and Genie returns a
conversation_id; every later question in the same chat is posted into it, so
follow-ups like "what about her attendance?" resolve against what was already
asked. Dropping that id and starting fresh each time — which this used to do —
makes the agent look amnesiac.
"""

from datetime import timedelta

from databricks.sdk import WorkspaceClient

# start_conversation_and_wait defaults to a 20-minute timeout, which just leaves
# the browser spinning if Genie is slow. Fail fast so the UI can say so.
GENIE_TIMEOUT = timedelta(seconds=55)


def _answer_text(message) -> str:
    for attachment in message.attachments or []:
        text = getattr(attachment, "text", None)
        content = getattr(text, "content", None) if text else None
        if content:
            return content
    return (
        "Genie answered but didn't return a text summary — it likely produced a result "
        "table instead. Open the Genie space directly to see the query result."
    )


def ask_genie(host: str, user_token: str, space_id: str, question: str,
              conversation_id: str | None = None) -> dict:
    # auth_type="pat" pins the SDK to this explicit user token — see the matching
    # comment in app.py's workspace_client() for why that's required on Apps.
    client = WorkspaceClient(host=host, token=user_token, auth_type="pat")

    if conversation_id:
        message = client.genie.create_message_and_wait(
            space_id=space_id,
            conversation_id=conversation_id,
            content=question,
            timeout=GENIE_TIMEOUT,
        )
    else:
        message = client.genie.start_conversation_and_wait(
            space_id=space_id,
            content=question,
            timeout=GENIE_TIMEOUT,
        )

    return {
        "answer": _answer_text(message),
        # Returned so the browser can keep the thread going. Falls back to whatever
        # was passed in, so a missing field never silently resets the conversation.
        "conversation_id": getattr(message, "conversation_id", None) or conversation_id,
    }
