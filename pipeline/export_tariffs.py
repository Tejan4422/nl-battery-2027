"""Copy config/tariffs_nl.yaml to web/public/data/tariffs.json for the website.

    uv run python -m pipeline.export_tariffs
"""

from model.tariffs import load_yaml, unverified_values

from .export import OUT_DIR, write_json


def main() -> None:
    cfg = load_yaml()
    write_json(cfg, OUT_DIR / "tariffs.json")
    todo = unverified_values(cfg)
    print(f"{len(todo)} unverified values: {', '.join(todo)}")


if __name__ == "__main__":
    main()
