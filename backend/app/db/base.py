from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import all models here so Alembic autogenerate picks them up.
# Person A's models:
from app.models.tenant import Tenant  # noqa: F401, E402
from app.models.user import User  # noqa: F401, E402
from app.models.audit_log import AuditLog  # noqa: F401, E402
from app.models.cost_event import CostEvent  # noqa: F401, E402

# Person B's models (imported after B adds their implementation):
# from app.models.cms import CmsPage  # noqa: F401, E402
# from app.models.chunk import ContentChunk  # noqa: F401, E402
# from app.models.widget import Widget  # noqa: F401, E402
# from app.models.conversation import Conversation, Message  # noqa: F401, E402
# from app.models.lead import Lead  # noqa: F401, E402
# from app.models.escalation import Escalation  # noqa: F401, E402

# Person C's models:
# from app.models.guardrail_config import GuardrailConfig  # noqa: F401, E402
