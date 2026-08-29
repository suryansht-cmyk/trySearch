"""Test schema setup.

Application code no longer creates tables — Alembic owns the schema as of T2. The
tests run against in-memory SQLite, where replaying the migration chain for every
session would be slow and would exercise Alembic rather than the code under test,
so the schema is built straight from the models here.

This runs at import, not in a fixture, and that ordering matters: pytest imports
conftest before the test modules, and each test module calls `import server_pg` at
*its* import time, which builds the app and reads the database identity. The tables
have to exist before that happens.

`alembic upgrade head` from empty is verified separately, against staging Postgres.
"""

import os

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('SECRET_KEY', 'test-secret')
# Bind the engine to in-memory SQLite before anything imports app.db. The test
# modules set this to the same value; whichever runs first wins and they agree.
os.environ['DATABASE_URL'] = 'sqlite://'

from app import models  # noqa: E402,F401 - registers the tables on `metadata`
from app.db import engine, metadata  # noqa: E402

metadata.create_all(engine)
