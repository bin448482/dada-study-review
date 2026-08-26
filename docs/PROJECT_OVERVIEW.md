# Project overview

Dada Study Review is a controlled workflow for English recitation entry and review. Deterministic code owns learning records, queue order, state transitions, scheduling, and recovery. State-machine LLM definitions perform English review, question generation, answer assessment, and child-facing feedback through strict structured contracts.

| Capability | Program owns | LLM owns |
| --- | --- | --- |
| Entry | Workflow, material records, learning items, re-entry records, and commit order | Language review, unit splitting, and concise correction guidance |
| Review | Due-item selection, frozen queue, question lock, assessment validation, scheduling, and archival | Question text, semantic assessment, accuracy, and feedback |
| Recovery | Event references, persisted terminal outcomes, and retry boundaries | No persistence, archive selection, or delivery authority |

## Deliberate boundaries

- The program never grades English by string matching, similarity, or reference-text rules.
- An LLM cannot select a learning item, change a schedule, execute SQL, access files, or send an uncommitted reply.
- Identity is an integration concern and must come from an exact static mapping, never chat text.
- Credentials, user content, delivery targets, and operational archives belong outside the source repository.

The reference includes offline runtime tests and a generic inbound plugin. It does not include a ready-to-run household deployment, model credentials, or a messaging-channel configuration.
