# Dada Study Review

MIT-licensed reference implementation for a controlled English learning workflow. Deterministic program code owns workflow state, learning records, frozen review queues, question locks, scheduling, recovery, and bounded persistence. State-machine Skills handle language review, question wording, semantic assessment, and child-facing feedback through versioned JSON contracts.

## What This Repository Provides

- Entry workflow for recording and auditing English learning material.
- Review workflow with frozen due-item queues, locked questions, assessment, scheduling, and recovery.
- Dialogue workflow with versioned Unit packages, target progress, scenarios, and review-point capture.
- Textbook screenshot-to-Markdown course maintenance Skill.
- Markdown-to-Dialogue-Unit candidate Skill with task audit, semantic review, validation, and immutable finalization.
- Provider-neutral TTS boundary with bounded MP3 output and text fallback.
- Generic OpenClaw inbound adapter source.
- Deterministic Python and Node tests using temporary data and fake gateways.

## Course Content Pipeline

The public course-maintenance workflow is:

```text
textbook screenshots
  -> page-split English Markdown
  -> Unit candidate
  -> task-audit and semantic-review
  -> candidate validation
  -> immutable Unit JSON
  -> Dialogue targets and question intents
```

Textbook images, private course workspaces, generated candidates, formal archives, identities, credentials, and delivery targets are not included. Supply course inputs from a separate local workspace and keep generated course data outside this repository unless it is intentionally reviewed for publication.

## Offline Verification

Requirements: Python 3.12+, Node.js 22.12+ with `node:sqlite`, and pnpm 9+.

The repository scripts use Bash. Linux and macOS can run them directly; Windows requires Git Bash or WSL. Native PowerShell users should provide equivalent wrappers while keeping the same workspace configuration model rather than hard-coding Windows or Unix paths.

The project workspace is defined by `config/workspace.json` (copy `config/workspace.example.json`). It owns the virtual environment, Node dependencies, course inputs, archive, logs, temporary files, and generated outputs. External deployment targets are configured separately and are not part of the workspace.

```bash
./scripts/bootstrap-workspace.sh
pnpm run test:offline
```

The offline suite uses temporary SQLite databases, synthetic scope data, and fake gateways. It does not contact model providers, TTS providers, OpenClaw Gateway, messaging channels, or a real learning archive.

## Initialize a SQLite Archive

After installing the dependencies, initialize a new archive with the public script:

```bash
./scripts/initialize-database.py
```

The script defaults to `<workspace archive>/workflow-v3.sqlite3`, as resolved from `config/workspace.json` or `config/workspace.example.json`. To choose an explicit path:

```bash
./scripts/initialize-database.py --database /path/to/workflow-v3.sqlite3
```

It creates or verifies the nine Dada business tables and the two LangGraph SQLite checkpoint tables, then runs `PRAGMA integrity_check` and `PRAGMA foreign_key_check`. The operation is idempotent and does not import learning material, create a child identity, or read credentials. Existing legacy archives require an explicit migration procedure; this command does not migrate or delete existing data. Entry, Review, and Dialogue services also call the same business-schema initializer when they start, while the standalone command makes the deployment step explicit.

## Optional Integrations

The repository contains anonymous configuration examples for a static child scope, OpenClaw routing, and optional TTS. Copy examples outside the repository, replace every example value, and keep credentials in a protected secret store. Real LLM, TTS, OpenClaw, and channel tests are explicit opt-in operations and are not part of the default CI gate.

The OpenClaw adapter is source code only. It requires a deployment-owned static identity mapping, archive directory, model transport, definition digests, and protected credentials. This repository provides no production deployment command and never supplies those values.

## Boundaries

This project is not a hosted service, a ready-to-run children's application, a shared archive platform, a messaging bot deployment, or a model-weights distribution. Offline tests prove the reusable reference implementation only; they do not prove a live provider or channel deployment.

## Security

Do not commit learning archives, session identifiers, API keys, cookies, delivery targets, textbook images, private course workspaces, or model request/response logs. The runtime keeps credentials out of SQLite events and checkpoint state, but operators remain responsible for host security, backups, transport, and access control.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) for contribution and security guidance.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Module design](docs/MODULE_DESIGN.md)
- [Database schema](docs/DATABASE_SCHEMA.md)
