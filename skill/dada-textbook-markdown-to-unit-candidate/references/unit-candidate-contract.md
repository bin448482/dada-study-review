# Unit Candidate Contract

The candidate is a strict `dada.dialogue_unit_definition v3` JSON object. v3 is the current page-semantic shape; the Unit itself still freezes course content through `version`. The active Unit 1 v1 package continues to load under its existing contract; a new candidate must not use a removed legacy content shape.

```json
{
  "definition_schema_version": 3,
  "unit_id": "caller-provided stable ID",
  "version": 1,
  "title": "caller-provided title",
  "source_pages": [14],
  "targets": [],
  "question_intents": [],
  "scenarios": [],
  "completion": {}
}
```

## Target Fields

Each target retains the Unit's base fields and adds these v3 fields:

| Field | Requirement |
| --- | --- |
| `source_refs` | Non-empty source list; each item contains `page`, `raw_heading`, and `source_locator`. Headings are for locating only, not strategy or type restrictions |
| `semantic_focus` | Briefly states the semantics or communication ability the child must understand |
| `dialogue_evidence` | `understand_and_respond`, `semantic_expression`, `initiate_question`, or `interaction` |
| `child_output_requirement` | Uses `respond`, `describe`, `ask`, or `interact` according to the evidence |

An v3 `phrase` target must also contain `review_design`. This is a bounded, versioned review design containing `schema_version`, review purpose, `context_kind`, information slots, prompt constraint, at least one `target_form` with `accepted_expressions`, and nullable `semantic_alternatives`. It may describe only semantics supported by the current page and Unit topic and must not contain a complete textbook transcription, archive, credentials, or hidden reasoning. `word`, `sentence`, and `function` must not carry this field.

`source_locator` is a block-location description of no more than 240 characters, such as “Look and say / question 2” or “timetable table / Monday”. It must not copy the full page Markdown, screenshot, complete answer, or model reasoning.

Target may be `word`, `phrase`, `sentence`, or `function`. Words, phrases, and sentences are teaching focuses and need not be repeated verbatim; `function` describes an observable communication ability. `reviewable` is still determined by target type: the first three are `true`, and `function` is `false`.

## Question Intent Fields

Each target has at least one question intent. In addition to base `intent_key`, `target_id`, `scenario_ids`, and `purpose`, it must contain `semantic_focus`, `dialogue_evidence`, and `prompt_constraint`. The intent evidence must match the target.

`initiate_question` `prompt_constraint` must include a role, purpose, and “information gap”; `understand_and_respond` must require a “complete answer”. Other evidence types must not treat the textbook sentence as an answer the child must memorize.

## Source and Multi-Solution Rules

- `source_refs.page` must belong to the Unit's `source_pages`, and `raw_heading` must be an actual second-level or deeper heading in the corresponding Markdown;
- `source_locator` may locate only a block and cannot use a heading as proof that a target qualifies;
- a target must be directly supported by the specified page's topic, task, or language material; natural semantic equivalents are allowed;
- every observable learner task on a page must have a target, scenario content slot, or coverage check; merged tasks must retain all information dimensions and cannot retain only a heading or broad topic word;
- model sentences, exercise sentences, learning-note expressions, and replaceable frames explicitly listed on a page are language material; reusable expressions must become separate targets or have an exclusion reason recorded in the coverage audit, not be silently absorbed by a broad function;
- every specified page and every second-level or deeper block must be classified as vocabulary, phrase/collocation, sentence/pattern, communication task, information relation, or explicit exclusion; do not silently skip a page or exercise block;
- explicit teaching material has priority over incidental body words; one-off factual answers, names, layout notes, and blanks require exclusion reasons;
- `initiate_question` must record the role, purpose, information gap, and information dimensions covered by the question; prompts such as `Who / What / How long / How many` must not be omitted during semantic extraction;
- the same textbook content may produce multiple valid candidates and need not exactly match a fixed JSON; the same learning result must not be duplicated within one Unit;
- references among `question_intents`, `scenarios`, `completion`, and targets must be bidirectionally consistent;
- do not write complete page Markdown, screenshot paths, archives, credentials, provider configuration, or hidden model reasoning.

## Finalization Boundary

The candidate must provide two sidecars:

- `unit.v<version>.candidate.task-audit.json`: page-by-page task/language-material coverage; it must cover every allowed page and every candidate target;
- `unit.v<version>.candidate.semantic-review.json`: target merge/split audit; it must bind the candidate content hash, cover every target, and have `retain_distinct` as every group's decision.

`validate-dialogue-unit-candidate.py` may validate the candidate alone, but `finalize-dialogue-unit-candidate.py` must receive and validate both sidecars. Only after all three pass may it atomically write the same-version `unit.v<version>.json`. It must fail when the final file already exists; no failure may leave a partial final package. A finalized version cannot be overwritten; another reasonable design must create a higher version.
