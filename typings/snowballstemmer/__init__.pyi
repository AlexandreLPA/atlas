"""Types for the part of snowballstemmer 3.1.1 that Atlas uses. It ships no type information."""

class Stemmer:
    def stemWord(self, word: str) -> str: ...  # noqa: N802

def stemmer(lang: str) -> Stemmer: ...
