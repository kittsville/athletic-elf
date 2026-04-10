"""Application entry point (wsgi / `python app.py`)."""

import os

from athletic_elf.factory import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port)
