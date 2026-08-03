"""Revision template used by `alembic revision --autogenerate`."""

revision = "${up_revision}"
down_revision = ${down_revision | repr(none)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
