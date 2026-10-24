"""X25519 identities (secret keys) and recipients (public keys) of the age format."""

from typing import Self

class Recipient:
    @classmethod
    def from_str(cls, value: str) -> Self: ...

class Identity:
    @classmethod
    def generate(cls) -> Self: ...
    @classmethod
    def from_str(cls, value: str) -> Self: ...
    def to_public(self) -> Recipient: ...
