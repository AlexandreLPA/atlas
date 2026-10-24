"""The browser: Atlas's own Chrome, and the code the model writes to drive it."""

from atlas.browser.errors import BrowserError
from atlas.browser.model import TOOL_NAME, BrowserMode
from atlas.browser.service import Browser, BrowserResult
from atlas.browser.store import ENGINE, find_chrome

__all__ = [
    "ENGINE",
    "TOOL_NAME",
    "Browser",
    "BrowserError",
    "BrowserMode",
    "BrowserResult",
    "find_chrome",
]
