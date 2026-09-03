# Coding Conventions

## Language

- **Code language**: English. All identifiers, docstrings, inline comments, and log messages must be in English.
- **User interface**: German (default), English (alternative). All UI strings must be in the translation catalogs at `src/app/static/i18n/de.json` and `src/app/static/i18n/en.json`. No hardcoded strings in HTML templates.
- **Domain enum values**: German (e.g. `"akzeptiert"`, `"offen"`). These are persisted in the database and are part of the API contract. Do not translate them.

## Code quality

- Human-readable code. Variable and function names must be self-documenting.
- Clean code principles: single responsibility, no side-effects in constructors, no magic numbers without named constants.
- Code is the developer's documentation. Comments explain *why*, not *what*.
- No multi-line docstring blocks that describe what the code obviously does. One-line docstrings for non-obvious functions only.
- No commented-out code. Remove dead code entirely.
- No feature flags or backwards-compatibility shims unless explicitly required.
- No premature abstractions: three similar lines is better than a premature helper.

## i18n

All frontend text must go through the i18n layer:

1. Add the key to both `de.json` and `en.json`.
2. Use `data-i18n="key"` attribute on the HTML element, or `t("key")` in JavaScript.
3. Keys use dot notation for namespacing: `admin.api_keys.title`, `analysis.options.reasoning`.

Verify zero gaps between catalogs before committing. The i18n engine falls back from German to English if a key is missing in German.

## Changelog

Every code change that affects observable behaviour must be documented in `CHANGELOG.md` under `[Unreleased]`. Include:

- What changed (one sentence)
- Why it was changed
- Which files were modified

## Documentation

The MkDocs documentation at `docs/` must be kept in sync with the code. When adding a new API endpoint, add a corresponding entry in the relevant `docs/api/*.md` file. When changing a database model, update `docs/architecture/database.md`.

All gaps in the documentation (missing endpoints, outdated descriptions, missing environment variables) must be closed when discovered.

## Error handling

- Validate at system boundaries (user input, external APIs). Trust internal code and framework guarantees.
- Do not add error handling for scenarios that cannot happen.
- FastAPI exception handlers are centralised in `src/app/main.py`.

## Testing

- Integration tests must hit a real database, not mocks (lesson learned from a past incident where mocked tests passed but the production migration failed).
