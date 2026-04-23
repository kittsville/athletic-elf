"""Combined ASGI app entry point.

Wiring lives in ``athletic_elf.mcp_app.build_asgi_app`` so tests can build the
same stack around a test Flask app without running ``create_app()``.

Production: ``gunicorn -k uvicorn.workers.UvicornWorker asgi:app`` (see Procfile).
"""

from athletic_elf.factory import create_app
from athletic_elf.mcp_app import build_asgi_app

flask_app = create_app()
app = build_asgi_app(flask_app)
