"""What can go wrong with the home, its settings and its database.

Every message says what went wrong and how to fix it.
"""


class HomeError(Exception):
    """Base for every error of the `home` subject."""


class TimeFormatError(HomeError, ValueError):
    """A time is naive, or its text is not in Atlas's UTC format."""


class SettingsError(HomeError):
    """A setting is missing, malformed, or in a place it must not be."""


class HomeFolderError(HomeError):
    """The home folder cannot be created, opened or made private."""


class DatabaseError(HomeError):
    """The database cannot be opened, or a migration cannot run."""


class MigrationEditedError(DatabaseError):
    """A migration changed after it ran. Migrations are forward-only: add a new one instead."""


class DatabaseTooNewError(DatabaseError):
    """The database was migrated by a newer Atlas than this one."""


class FileBusyError(HomeError):
    """Another process held a shared file's lock for too long."""


class HomeNameError(HomeError, ValueError):
    """A home's name breaks the rule, or names no home."""


class HomeBusyError(HomeError):
    """A server serves the home, so it cannot be served again, renamed or deleted now."""


class BackupError(HomeError):
    """A backup cannot be written, read, checked or restored."""


class PictureError(HomeError, ValueError):
    """A home's picture that is not a PNG, or weighs too much."""
