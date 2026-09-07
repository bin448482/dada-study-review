# Architecture

The runtime separates deterministic program state from language-model decisions.

| Layer | Owns |
| --- | --- |
| `v3_workflow` | SQLite business facts, review queue snapshots, policy evaluation, migration, and model-turn retry lifecycle. |
| `v3_entry` | Controlled entry Graph, input/result contracts, and injected credential-free model gateway port. |
| `v3_review` | Controlled review Graph, frozen item progression, question locks, structured assessments, and scheduling handoff. |
| `v3_dialogue` | Controlled Dialogue Graph, versioned Unit loading, target progress, and injected credential-free model gateway port. |
| `*_production` | Definition-digest verification plus injected HTTPS transport; credentials remain process environment only. |
| `inbound_v3_workflow_hook` | Generic exact-static-session routing and production CLI process boundary. |
| `skill/` | Idle intent and Entry/Review/Dialogue state-machine LLM behavior definitions. |

No source component should infer identity from chat text, let an LLM select a learning item, or use reference English for program-side automatic grading. A deployment must provide its own exact static identity mapping and must keep all real archive and credential material outside this repository.

Read the [documentation index](README.md) for the domain model, contracts, decisions, development workflow, and optional OpenClaw integration.
