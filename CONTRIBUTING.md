# Contributing

Use Python 3.12+, install the development extras and run Ruff and pytest before pushing. Keep domain logic outside HTTP handlers and vendor fields inside adapters. Changes to canonical money, tax calculations, identities, transitions or external writes need regression tests demonstrating duplicate and failure behavior.

Never commit credentials, raw customer invoices or personal identity evidence. Add migrations when changing an existing schema. Document unsupported vendor behavior explicitly and use a real sandbox acceptance record before calling an integration production-ready. Propose scope changes against `docs/scope-coverage.md`.
