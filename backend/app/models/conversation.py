# TODO: Person B — implement Conversation and Message models
# Conversation fields: id, tenant_id, widget_id, visitor_session_id, status, created_at, updated_at
# Message fields: id, tenant_id, conversation_id, role (visitor/assistant/tool/system),
#                 content_redacted, metadata jsonb, created_at
# Then uncomment the import in backend/app/db/base.py

import enum


class MessageRole(str, enum.Enum):
    visitor = "visitor"
    assistant = "assistant"
    tool = "tool"
    system = "system"
