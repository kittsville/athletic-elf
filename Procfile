release: flask --app app init-db
web: gunicorn -k uvicorn.workers.UvicornWorker asgi:app
