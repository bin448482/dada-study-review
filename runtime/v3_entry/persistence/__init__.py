"""Fixed SQLite persistence boundary for the v3 entry runtime."""

from .repository import EntryRepository, RepositoryError

__all__ = ["EntryRepository", "RepositoryError"]
