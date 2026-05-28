"""SQLAlchemy declarative base.

Model classes should import Base from here, NOT from this module itself.
All model registrations for Alembic autogenerate are done in migrations/env.py.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Register every model with ``Base.metadata`` so Alembic autogenerate sees
# them. The ``import app.models.X`` form (rather than ``from … import X``)
# is intentional — it tolerates being re-entered mid-way through another
# model's import without raising ``ImportError`` on a partially-defined
# class.
import app.models.audit_log  # noqa: F401, E402
import app.models.chunk  # noqa: F401, E402
import app.models.cms  # noqa: F401, E402
import app.models.conversation  # noqa: F401, E402
import app.models.cost_event  # noqa: F401, E402
import app.models.escalation  # noqa: F401, E402
import app.models.lead  # noqa: F401, E402
import app.models.tenant  # noqa: F401, E402
import app.models.user  # noqa: F401, E402
import app.models.widget  # noqa: F401, E402

# Person C's models:
# import app.models.guardrail_config  # noqa: F401, E402
