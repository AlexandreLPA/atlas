"""The server token a container keeps (docs/ARCHITECTURE.md, Container).

A server Atlas runs off loopback, which today means in a container, keeps its token in the vault,
in the data volume, made on the first boot and never written over: whoever reaches the port needs
it, and `atlas server token` hands it to the user (Atlas V3's `atlas instancia token`). A token
given in `ATLAS_SERVER_TOKEN` still wins, and a loopback server given none makes a fresh one that
nobody knows, as before.
"""

import secrets

from atlas.secrets import Vault

#: The token's name in the vault, the same as the variable that gives it.
KEPT_TOKEN = "ATLAS_SERVER_TOKEN"  # noqa: S105 - a secret's name, not a secret


def kept_token(vault: Vault) -> str:
    """The token kept in the vault, made the first time it is asked for."""
    return vault.keep(KEPT_TOKEN, secrets.token_urlsafe(32))
