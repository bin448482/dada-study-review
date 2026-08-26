# Dada Study Review

MIT-licensed reference implementation of a controlled English recitation entry and review workflow. The program owns archive state, frozen review queues, question locks, scheduling, and recovery; LLM definitions handle language review and child-facing feedback through structured contracts.

## Included and excluded

This release includes the reusable Python runtime, state-machine Skill definitions, review policies, generic OpenClaw inbound plugin source, and deterministic tests. It deliberately excludes learning archives, identities, credentials, deployed Skill mirrors, real model evaluation configuration, delivery targets, and household-specific operations.

The OpenClaw plugin is source code only. Configure it with your own static identity mapping, storage directory, model endpoint, definition digests, and protected credentials. This repository provides no production deployment command and never supplies those values.

## Quick start: offline verification

Requirements: Python 3.12+, Node.js 22.12+ (for `node:sqlite`), and pnpm 9+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
pnpm install
pnpm run test:offline
```

The offline suite uses temporary SQLite databases and fake gateways. It does not contact a model provider, OpenClaw Gateway, messaging channel, or a real learning archive.

## Optional integration

`config/examples/` contains anonymous shape-only examples for one static child scope and the OpenClaw plugin configuration. Copy them outside the repository, replace every example value, and keep credentials in your deployment secret store. Run `scripts/definition_digest.py` after changing a state-machine definition and configure the resulting digest in your own plugin configuration.

## Security and privacy

Do not commit learning archives, session identifiers, API keys, cookies, delivery targets, or model request/response logs. The runtime deliberately keeps credentials out of SQLite events and checkpoint state, but operators remain responsible for their own host, backups, transport, and access controls.

See [architecture notes](docs/ARCHITECTURE.md), [contributing](CONTRIBUTING.md), and [security reporting](SECURITY.md).
