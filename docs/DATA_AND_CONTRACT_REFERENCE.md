# Data and contract reference

SQLite is the authoritative business store. A deployment chooses its own protected archive location and exact static scope mapping.

| Table | Purpose | Important invariant |
| --- | --- | --- |
| `workflows` | Current Entry or Review snapshot | At most one active workflow per external scope |
| `workflow_log_events` | Ordered immutable event history | References stay within the same workflow and point backwards |
| `learning_materials` | Material lifecycle and audit result | Draft material never enters Review |
| `learning_items` | Individual progress and revision | Program owns stage, due time, completion, and revision |
| `review_queue_items` | Frozen Review-round order | Only the current item advances after a valid assessment |

## State-machine contracts

| Contract | Current version | Purpose |
| --- | --- | --- |
| `dada.workflow_log_event` | v1 | Persisted event envelope |
| `dada.entry_state_machine_turn` | v1 | Fixed Entry-model input |
| `dada.entry_state_machine_result` | v3 | Entry-model structured result |
| `dada.entry_audit` | v3 | Materials, learning items, and re-entry result |
| `dada.review_state_machine_turn` | v5 | Fixed Review-model input |
| `dada.review_state_machine_result` | v4 | Review question, assessment, or transition result |
| `dada.review_assessment` | v1 | Accuracy and feedback used for program scheduling |
| `dada.dialogue_state_machine_turn` | v1 | Fixed Dialogue-model input |
| `dada.dialogue_state_machine_result` | v1 | Dialogue-model structured result |

The program validates every result before it has business effect. Empty question prompts cannot be locked or delivered. A question lock and its visible delivery text are committed together; recovery replays that committed text rather than asking the model again. Checkpoint data is control-only and must not contain user content or credentials.
