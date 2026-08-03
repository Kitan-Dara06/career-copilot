"""Add UNIQUE constraint on professors.name to support ON CONFLICT upserts.

The watch_add flow uses ON CONFLICT (name) DO NOTHING, which requires
a unique constraint or index on the name column.
"""

from __future__ import annotations

from typing import ClassVar

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels: ClassVar[set[str] | None] = None
depends_on: ClassVar[set[str] | None] = None


def upgrade() -> None:
    """Add UNIQUE constraint on professors.name."""
    op.create_unique_constraint("uq_professors_name", "professors", ["name"])


def downgrade() -> None:
    """Remove UNIQUE constraint on professors.name."""
    op.drop_constraint("uq_professors_name", "professors", type_="unique")
