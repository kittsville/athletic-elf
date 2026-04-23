"""Application entry point.

``app`` (the Flask WSGI app) is exported for ``flask --app app init-db``. When
run directly (``python app.py``), serves the combined Flask+MCP ASGI app at
``asgi:app`` via uvicorn so ``/mcp`` works in local dev.
"""

import os

from athletic_elf.factory import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 80))
    uvicorn.run("asgi:app", host="0.0.0.0", port=port)
