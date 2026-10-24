"""An attached image described for a conversation whose model does not see images (step 16g).

Before the turn, a model that sees images describes each one, and the description goes to the
conversation's model in its place.

The description models are the title models, side calls with the title quarantine. The description
is kept with the attachment, so it is made once, when the message is sent.
"""

import logging

from atlas.assistant.titles import TITLE_MODELS, TitleQuarantine
from atlas.models import (
    Image,
    ModelAuthError,
    ModelOutOfCreditError,
    ModelRequest,
    Models,
    ModelsError,
    SystemMessage,
    UserMessage,
)

#: The call ledger's name for a description's call.
DESCRIPTION_PURPOSE = "image_description"
#: What the description model is asked for each image.
DESCRIPTION_PROMPT = (
    "Describe this image briefly, in 2 to 4 sentences (about 150 English words or 200 Chinese "
    "characters). Give its main subject, the text, data or code that matters in it, and its "
    "context. For a chart, diagram or scientific figure, give the important labels, the legend "
    "and the key values. Leave out what is only decoration."
)
#: Who the description model is for. It keeps
#: a description in the image's own words, never an answer to the user.
DESCRIPTION_SYSTEM = (
    "You describe an image a user attached for a model that cannot see images. Describe only what "
    "the image shows; do not answer anything else."
)
#: Room for the ~150 words asked, without letting a runaway reply cost much.
DESCRIPTION_MAX_TOKENS = 600

_logger = logging.getLogger("atlas.assistant")


async def describe_image(
    models: Models, image: Image, scope: str | None = None, *, quarantine: TitleQuarantine
) -> str | None:
    """What a description model sees in `image`, from the first whose provider has a key, is not
    in `quarantine`, and answers; `None` when none can."""
    try:
        keyed = {provider.id for provider, stored in models.keyed() if stored}
    except ModelsError as error:
        _logger.warning("No description model could be chosen: %s", error)
        return None
    request = ModelRequest(
        (SystemMessage(DESCRIPTION_SYSTEM), UserMessage(DESCRIPTION_PROMPT, images=(image,))),
        purpose=DESCRIPTION_PURPOSE,
        scope=scope,
        max_output_tokens=DESCRIPTION_MAX_TOKENS,
        reasoning=False,
    )
    for provider, model in TITLE_MODELS:
        if provider not in keyed or quarantine.held(provider):
            continue
        try:
            reply = await models.client(provider, model).reply(request)
        except (ModelOutOfCreditError, ModelAuthError) as error:
            quarantine.hold(provider)
            _logger.warning("A description model failed: %s", error, extra={"provider": provider})
            continue
        except ModelsError as error:
            _logger.warning("A description model failed: %s", error, extra={"provider": provider})
            continue
        text = reply.text.strip()
        if text:
            return text
    return None
