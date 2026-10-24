"""What can go wrong with bots. Every message says what went wrong and how to fix it."""


class BotError(Exception):
    """A bot could not be read, made or removed: a broken definition, a name that cannot be a
    bot's, or one that is taken or missing."""
