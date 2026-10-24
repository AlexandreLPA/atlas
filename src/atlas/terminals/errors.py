"""The server terminal's errors."""


class TerminalError(Exception):
    """A terminal could not be opened or reached; the message says why, in the user's words."""


class TerminalUnavailableError(TerminalError):
    """This machine cannot open a terminal: its pseudo-terminal package is missing."""
