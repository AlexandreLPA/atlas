"""What can go wrong with skills. Every message says what went wrong and how to fix it."""


class SkillError(Exception):
    """A skill could not be read: broken frontmatter, or a name that cannot be a folder's."""


class SkillChangeError(SkillError):
    """A change to the skills was refused or failed, and nothing of it was applied: a write,
    a revert, an archive or a restore."""
