"""What can go wrong with the installation: its checkout, its build, its update."""


class InstallationError(Exception):
    """The installation cannot do what was asked; the message says how to fix it."""


class InstallationBusyError(InstallationError):
    """Another update or build of the app is running."""
