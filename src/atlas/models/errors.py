"""What can go wrong with a model call or the model catalog.

Every message says what went wrong and how to fix it. The kinds are the ones a caller treats
differently: a missing key is fixed by the user, a rate limit passes, an empty account does not.
"""


class ModelsError(Exception):
    """Base for every error of the `models` subject."""


class CatalogError(ModelsError):
    """The model catalog cannot be read, or the user's models.toml is wrong."""


class UnknownProviderError(CatalogError):
    """A provider nobody declared."""


class ModelChoiceError(ModelsError):
    """No model named, or a model that cannot do what the request asks."""


class ModelKeyError(ModelsError):
    """The provider's API key is not in the vault, or the vault cannot open it."""


class ModelRequestError(ModelsError):
    """The request cannot go to this provider as it is. Nothing was sent, or the provider refused
    it (HTTP 400, 404, 422)."""


class ModelAuthError(ModelsError):
    """The provider refused the key (HTTP 401 or 403)."""


class ModelOutOfCreditError(ModelsError):
    """The account's credit or quota is used up. It does not pass in seconds: credit, a bigger
    plan, another provider or the provider's own reset does."""


class ModelRateLimitError(ModelsError):
    """Too many requests for now (HTTP 429). It passes."""


class ModelUnavailableError(ModelsError):
    """The provider could not be reached, failed (HTTP 5xx), or cut the reply short."""
