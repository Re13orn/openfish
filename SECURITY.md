# Security Policy

## Supported Versions

This project is currently in early stage. Security fixes are applied on the latest `main` branch first.

## Reporting a Vulnerability

Please do not open a public issue for sensitive vulnerabilities.

Use one of these channels:

1. GitHub private vulnerability report (preferred).
2. If private report is unavailable, open an issue with minimal detail and request a private contact channel.

When reporting, include:

- affected commit/version,
- reproduction steps,
- impact assessment,
- suggested mitigation (optional).

## Security Notes for OpenFish Deployments

- Never commit `.env`, runtime logs, or SQLite runtime data.
- Rotate `TELEGRAM_BOT_TOKEN` immediately if exposed.
- Keep `ALLOWED_TELEGRAM_USER_IDS` strict and minimal.
- Keep project `allowed_directories` as narrow as possible.
