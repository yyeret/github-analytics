# Contributing

Use Python 3.13 for the hosted Worker. Clone or fork the repository, create a
virtual environment, and install `requirements-dev.txt` for local tests. Install
the GitHub CLI and authenticate it for public repository collection. Gemini is
optional; tests use fakes and do not require credentials.

Before submitting changes, run:

```text
python -m ruff check .
python -m pytest --cov --cov-report=term-missing
```

Use a separate environment with `requirements-security.txt` installed to run
`python scripts/security-check.py`. Read `SECURITY.md` before reporting a
vulnerability. Keep example data synthetic or clearly attributable to public
sources; never attach private repository exports, account records, or keys.

Describe the behavior changed and the checks run in your pull request. Preserve
the distinction between stated intent and achieved value in metrics and UI copy.
Contributions must be your own work or include compatible licensing and attribution.
