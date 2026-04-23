"""Combined ASGI app: Flask (WSGI) + FastMCP (Streamable HTTP) at ``/mcp``.

Uses FastMCP's Starlette app as the root so its session-manager lifespan fires,
wraps the ``/mcp`` route with ``AuthMiddleware`` (so auth applies only there),
and appends a catch-all mount that delegates everything else to Flask via WSGI.

Production: ``gunicorn -k uvicorn.workers.UvicornWorker asgi:app`` (see Procfile).
"""

from asgiref.wsgi import WsgiToAsgi
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from athletic_elf.factory import create_app
from athletic_elf.mcp_app import AuthMiddleware, build_mcp

flask_app = create_app()
flask_asgi = WsgiToAsgi(flask_app)

mcp = build_mcp()
app = mcp.streamable_http_app()

# Wrap the /mcp route: CORS outermost so preflight OPTIONS and 401 bodies carry
# CORS headers; auth inside so tools see the current athlete.
_mcp_route = app.router.routes[0]
_mcp_route.app = CORSMiddleware(
    AuthMiddleware(_mcp_route.app, flask_app),
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["mcp-session-id"],
)

# Catch-all: anything that isn't /mcp falls through to Flask.
app.router.routes.append(Mount("/", app=flask_asgi))
