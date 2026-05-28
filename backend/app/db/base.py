"""SQLAlchemy declarative base.

Model classes should import Base from here, NOT from this module itself.
All model registrations for Alembic autogenerate are done in migrations/env.py.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
