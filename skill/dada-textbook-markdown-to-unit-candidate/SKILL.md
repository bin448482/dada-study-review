---
name: dada-textbook-markdown-to-unit-candidate
description: "Design, validate, and finalize a Dialogue Unit candidate from a Unit topic and page-split English textbook Markdown in an explicit course workspace. Use for course maintenance when generating targets, scenes, and question intents; do not use for screenshots, archives, runtime delivery, or production enablement."
---

# Textbook Markdown to Dialogue Unit Candidate

Input is a completed Unit `english-text/` directory in an explicit course workspace, plus the caller-provided `unit_id`, `title`, `version`, and allowed source pages. Output is a v3 Unit candidate, structured task-audit, structured semantic-review, and an immutable same-version Unit JSON generated only after all three validate. This is a course-maintenance Skill, not a child-facing Dada Skill: do not read or write learning archives, screenshots, OpenClaw configuration, credentials, Gateway state, or the production allowlist.

Target generation follows a constrained multi-solution principle: the same textbook content may produce multiple valid target partitions, English anchors, or question intents, and need not exactly match a reference JSON; every result must have traceable textbook sources, independent and reusable semantics, natural Dialogue evidence, and successful deduplication, reference, contract, and version validation. Once finalized, an existing version must not be overwritten; another reasonable design must be a candidate for a new version.

## Required Workflow

1. Read the Unit README and only the allowed page Markdown files. Stop if a selected page is missing, empty, contains unresolved transcription markers, or conflicts with the requested page numbers.
2. Read each page's topic, headings, instructions, images/tables, model language, and questions in full. A heading is context for locating and understanding a teaching task, not a fixed strategy name, target-type allowlist, or evidence decider; unfamiliar headings and variants must not fail by wording alone.
3. Build a “page teaching-task inventory” before designing targets. For each task record at least: page/block location, the action the textbook asks the child to perform, information objects or semantic dimensions, child output mode, and whether it is required, invited practice, an example, or background. Do not record only the heading or extracted English words.
4. Audit task coverage: every task with a learner action must map to at least one target, scenario content slot, or explicit coverage check. If it is merged into an existing target, explain the covered semantic dimensions; do not lose a task because its heading or question wording changed. An unexplained task inventory is an extraction failure.
5. Then choose `word`, `phrase`, `sentence`, or `function` from the task semantics. Natural semantic equivalents are allowed, but `semantic_focus` and `source_refs` must explain the semantic bridge from page task to target; do not introduce content unsupported by the page task, Unit topic, or scenario.
6. Inventory page language materials separately: model dialogue sentences, exercise sentences, expressions explicitly listed in `My learning notes`/phrase lists, replaceable sentence frames, and grouped question words. Reusable frames or expressions must not be silently discarded under a function; make them a separate `phrase`/`sentence` target or explain in the task-coverage audit why they are only scenario scaffolding/examples. One-off names, factual answers, greetings, and purely operational instructions may be excluded, but the reason is required.
7. Add `source_refs`, `semantic_focus`, `dialogue_evidence`, and `child_output_requirement` to each target. Each `phrase` must also include bounded `review_design`:
   review purpose, context kind, information slots, prompt constraint, at least one target-form example, and optional semantic alternatives. A textbook question normally tests a complete answer after understanding; use `initiate_question` only when the task requires or explicitly invites the child to ask a question. Preserve the role, purpose, and information gap, and list the information dimensions covered by the question; do not write only “ask a question”.
8. Determine targets first, then generate same-topic scenarios and question intents. Each target needs at least one intent; references among scenarios, targets, intents, and completion must be bidirectionally consistent. One intent must not stand in for another uncovered page task.
9. Deduplicate semantics across pages. Keep one stable target for the same learning result; different but reasonable partitions may remain as candidate versions or review alternatives, but do not duplicate a learning point in one Unit. A merged target must cover all information dimensions and reusable frames from the merged tasks.
10. Generate `unit.v<version>.candidate.json`, `unit.v<version>.candidate.task-audit.json`, and `unit.v<version>.candidate.semantic-review.json` in `config/dialogue-units/<unit-id>/`. The candidate stores only the minimum target contract and page sources and does not generate a heading strategy report; the two sidecars are structured pre-finalization audit outputs.
11. Run `scripts/validate-dialogue-unit-candidate.py` with both audit files, then run `scripts/finalize-dialogue-unit-candidate.py`. If either step fails, retain the candidate and audit outputs, return the exact reason, and do not write the final Unit package.

Never overwrite an existing final Unit version. After finalization, do not modify the frozen Unit, publish a Skill, or enable a Unit.

Read [references/unit-candidate-contract.md](references/unit-candidate-contract.md) before generating a candidate. This contract does not depend on a heading strategy file.

## Minimum Page Teaching-Task Inventory

Build an inventory for each page internally before outputting the final JSON. The inventory is not written to the Unit package, but it must answer what the page asks the child to do and which target or scenario covers each requirement:

```text
page: 16
locator: B1 Look and say / question prompts
learner_action: ask for club information
information_dimensions: concrete information such as people, activities, duration, and number of people
output: ask
modality: invited_practice
coverage: ask_for_specific_club_information (initiate_question)
```

`learner_action` must not be written as “learn Look and say”; it must be an observable action such as identify, answer, compare, give a reason, ask, clarify, or interact. Do not omit `information_dimensions`: prompts such as `Who / What / How long / How many` must remain in the semantic focus and question intent even when they share one function target.

### Precision Threshold

- When a page has multiple independent learning actions, do not generate one unbounded target for “talk about the page topic”; merge only when one target clearly covers every action and information dimension.
- When a page contains action words such as “ask questions”, “compare”, or “give reasons”, record them in the inventory; do not extract only the neighboring nouns.
- `may / can / try` signals invited practice, not mandatory memorization, but decide whether it is a function target, scenario opportunity, or content slot and leave an explainable coverage relation in the candidate.
- Each `initiate_question` intent must specify the role, purpose, information gap, and allowed question dimensions; a complete textbook question alone is not a sufficient scaffold.
- Replaceable expressions explicitly listed in `My learning notes`, exercises, and model dialogues must be recorded individually; for example, `Can/May I ask some questions about ...?` and `How can I join the ... club?` cannot be silently covered by one “ask for information” function.
- Before finishing, check each “page task → target/scenario coverage → Dialogue evidence → question intent” path. A candidate must not be finalized while any task has no destination.

## Two Structured Audit Outputs

`task-audit` must be a JSON object containing `audit_schema_version: 1`, Unit/version/source_pages, and `pages[]`. Each page needs at least one section; each section must record `locator`, `section_kind`, `learner_action`, `information_dimensions`, `output`, `modality`, `coverage_target_ids`, `coverage_scenario_ids`, and `exclusion_reason`. Every allowed page must appear and every candidate target must be covered; a section without target/scenario coverage must state its exclusion reason.

`semantic-review` must be a JSON object containing `review_schema_version: 1`, Unit/version, the candidate's `candidate_content_hash`, `review_status: complete`, and `groups[]`. Each group records `group_id`, the complete `target_ids`, `decision`, and `rationale`; each target must occur in exactly one group. Finalize only when every group is `retain_distinct`; if `merge` or `remove` exists, modify the candidate and regenerate the audits first.

## Page-by-Page, Block-by-Block Extraction Checklist

Perform one complete scan for every page specified by the caller; do not skip a page because its heading is familiar, its content is short, or it appears to be only an exercise. Classify every second-level or deeper block into at least one category below and record its location and handling result in the inventory:

| Block category | Materials to check | Typical result |
| --- | --- | --- |
| Topic vocabulary | Image/table labels, bold words, vocabulary lists, repeatedly focused nouns or verbs | `word` target, or explain that it is one-off background vocabulary |
| Phrases and collocations | Phrase lists, verb collocations, fixed expressions, replaceable components | `phrase` target; do not retain only the individual verb |
| Sentences and frames | Model dialogues, exercise sentences, question prompts, `My learning notes`, sentence frames | `sentence` target, or record a clear reason for excluding it as scaffolding/example |
| Communication tasks | Learner actions such as ask, answer, compare, describe, give reasons, and role-play | `function` target or scenario task; Dialogue evidence is required |
| Information relations | Time, place, people, quantity, order, sameness/difference, cause and result | Put into semantic focus, question intent, or content slots; do not replace it with a broad topic word |
| Non-target material | Names, one-off factual answers, layout notes, blanks, repeated review prompts | Record the exclusion reason explicitly; do not discard silently |

After completing the inventory, confirm page-level completeness: every page must have at least one target, scenario content slot, or explicit conclusion that “this page produces no target”; the latter must explain why every block is only background, formatting, or coverage checking. Any unclassified block, unexplained reusable frame, or uncovered learner action is an extraction failure.

### Evidence Strength and Target Granularity

Decide whether to create a learning point by the following evidence strength, not by word frequency:

1. Explicit teaching material: phrases below headings, bold/listed expressions, model sentences, exercise sentences, question prompts, and learning notes have priority for the candidate;
2. Task-supported semantics: comparisons, descriptions, questions, or inferences jointly required by page questions, tables, or images may generalize into a function or sentence target;
3. Incidental body words, one-off answers, and purely layout text do not automatically become targets.

Decide first what semantics the child must master, then represent it as `word`, `phrase`, `sentence`, or `function`. The same semantics may have different valid partitions, but merging must not conceal any explicit teaching material.
