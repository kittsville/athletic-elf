"""DATABASE_URL normalization for the installed Postgres driver."""

import unittest

from athletic_elf.config import sqlalchemy_database_uri


class TestSqlalchemyDatabaseUri(unittest.TestCase):
    def test_default_uses_psycopg2(self):
        self.assertEqual(
            sqlalchemy_database_uri("postgresql://strava:strava@localhost:5432/strava"),
            "postgresql+psycopg2://strava:strava@localhost:5432/strava",
        )

    def test_coolify_postgres_scheme(self):
        self.assertEqual(
            sqlalchemy_database_uri("postgres://user:pass@db:5432/strava"),
            "postgresql+psycopg2://user:pass@db:5432/strava",
        )

    def test_explicit_driver_unchanged(self):
        url = "postgresql+psycopg://user:pass@db:5432/strava"
        self.assertEqual(sqlalchemy_database_uri(url), url)

    def test_sqlite_unchanged(self):
        self.assertEqual(
            sqlalchemy_database_uri("sqlite:///:memory:"), "sqlite:///:memory:"
        )
