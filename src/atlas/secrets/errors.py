"""What can go wrong with the vault. Every message says what went wrong and how to fix it."""


class SecretsError(Exception):
    """Base for every error of the `secrets` subject."""


class InvalidSecretNameError(SecretsError, ValueError):
    """A secret name with characters outside the allowed set."""


class VaultDamagedError(SecretsError):
    """The vault file is not a vault: not JSON, wrong shape, or from a newer Atlas.

    A damaged vault is never read as empty: an empty reading would let the next write replace it,
    and every secret in it would be gone. Atlas V3 had exactly that defect.
    """


class VaultLockedError(SecretsError):
    """The whole vault cannot open here, for one reason: no key, or the key of another vault."""


class SecretUnreadableError(SecretsError):
    """One entry cannot be decrypted. The other entries are not affected."""
