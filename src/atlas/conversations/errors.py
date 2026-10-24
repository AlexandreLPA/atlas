"""What can go wrong with stored conversations. Every message says what to do about it."""


class ConversationsError(Exception):
    """Base for every error of the `conversations` subject."""


class ConversationNotFoundError(ConversationsError):
    """No conversation has the id that was asked for."""


class StoredMessageError(ConversationsError):
    """A stored message cannot be read back as a message."""


class AttachmentError(ConversationsError):
    """A file could not be attached: too heavy, cut short, or gone."""
