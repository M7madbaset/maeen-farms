"""Write the OpenAPI spec without starting a server:  python -m maeen.export_openapi docs/openapi.json"""
from __future__ import annotations
import json, sys
from .api import create_app
from .runtime import build_runtime


def main(path: str = "docs/openapi.json") -> None:
    app = create_app(build_runtime("40"), "x" * 40, enable_docs=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(app.openapi(), f, indent=2, ensure_ascii=False, sort_keys=True)
    print("written", path)


if __name__ == "__main__":
    main(*sys.argv[1:2])
