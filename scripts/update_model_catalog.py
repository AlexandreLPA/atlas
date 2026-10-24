"""Refresh the model catalog shipped with Atlas from models.dev.

    uv run python scripts/update_model_catalog.py

models.dev (https://models.dev, MIT) is an open database of what each model can do and what it
costs, kept by its community. This script keeps only the
providers named by `catalog` in providers.toml and only the fields Atlas reads, and writes them in a
stable order, so a refresh is a reviewable diff. Prices are US dollars per million tokens.

The fields keep models.dev's names, with one exception: `reasoning_options` becomes `efforts`, the
effort levels a model accepts (`none`, `low`, ...), which tells a model that refuses `none` apart.
"""

import json
import sys
import tomllib
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

SOURCE = "https://models.dev/api.json"
DATA = Path(__file__).resolve().parents[1] / "src" / "atlas" / "models" / "data"
#: The fields Atlas reads. Everything else in models.dev is left out.
KEPT = ("name", "tool_call", "reasoning", "modalities", "limit", "cost", "last_updated", "status")


def main() -> int:
    with (DATA / "providers.toml").open("rb") as handle:
        providers = tomllib.load(handle)["providers"]
    wanted = sorted({table["catalog"] for table in providers.values() if "catalog" in table})
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "atlas-agent"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        source: dict[str, Any] = json.load(response)
    catalogs = {name: _models(source[name]["models"]) for name in wanted}
    document = {
        "source": SOURCE,
        "license": "MIT, the models.dev contributors",
        "fetched_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "catalogs": catalogs,
    }
    target = DATA / "models.json"
    target.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    count = sum(len(models) for models in catalogs.values())
    sys.stdout.write(f"Wrote {count} models from {', '.join(wanted)} to {target}.\n")
    return 0


def _models(models: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for model_id, entry in sorted(models.items()):
        data = cast("dict[str, Any]", entry)
        model = {key: data[key] for key in KEPT if key in data}
        cost = model.get("cost")
        if isinstance(cost, dict):
            # `context_over_200k` repeats the first tier under an older name.
            model["cost"] = {
                key: value
                for key, value in cast("dict[str, Any]", cost).items()
                if key != "context_over_200k"
            }
        modalities = model.get("modalities")
        if isinstance(modalities, dict):
            model["modalities"] = {"input": cast("dict[str, Any]", modalities).get("input", [])}
        efforts = _efforts(data.get("reasoning_options"))
        if efforts is not None:
            model["efforts"] = efforts
        kept[model_id] = model
    return kept


def _efforts(options: object) -> list[str] | None:
    if not isinstance(options, list):
        return None
    for option in cast("list[Any]", options):
        if isinstance(option, dict) and option.get("type") == "effort":
            return [str(value) for value in cast("dict[str, Any]", option).get("values", [])]
    return None


if __name__ == "__main__":
    sys.exit(main())
