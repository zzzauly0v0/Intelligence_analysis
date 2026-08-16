"""SQLAlchemy models.

Importing this package registers every mapper, so ``Base.metadata`` is complete
for Alembic and string-based relationships (``User.sessions``) can resolve.
"""

from app.db.base import Base
from app.db.models.session import Session
from app.db.models.user import User, UserRole

__all__ = ["Base", "Session", "User", "UserRole"]
