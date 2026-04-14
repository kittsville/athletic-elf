# Instructions for coding agents

Before finishing a change (especially opening or updating a PR), **verify both tests and formatting** the same way CI does. See `README.md` (Tests section) and `.github/workflows/tests.yml`.

## Tests

From the **repository root**, with dependencies installed (`pip install -r requirements.txt`):

```bash
python3 -m unittest discover -s tests -v
```

If you use a venv at `.venv`, use that interpreter so imports match what you installed:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

CI runs: `python -m unittest discover -s tests -v` after `pip install -r requirements.txt` (see the `test` job in `.github/workflows/tests.yml`). Fix any failures before considering the work done.

## Formatting (Ruff)

Install dev tooling once:

```bash
pip install -r requirements-dev.txt
```

**Check** formatting (must pass — matches CI `format` job):

```bash
ruff format --check .
```

**Apply** formatting when files need reformatting:

```bash
ruff format .
```

CI runs `ruff format --check .` in `.github/workflows/tests.yml`; do not skip this after editing Python files.
