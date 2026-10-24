"""What can go wrong with the browser."""


class BrowserError(Exception):
    """The browser or its engine could not start or go on; the message says why."""
