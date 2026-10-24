"""What can go wrong in the agent loop. Every message says what went wrong and how to fix it.

A model's failure (no credit, a refused request) is not an error of the loop: the turn ends with
an outcome that says so, and the history stays well formed. These exceptions are for the loop's
own misuse.
"""


class AgentError(Exception):
    """Base for every error of the `agent` subject."""


class ToolSetError(AgentError):
    """Two tools share a name, so the model's calls could not tell them apart."""
