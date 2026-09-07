---
name: dada-textbook-screenshot-to-markdown
description: "Transcribe textbook screenshots from an explicitly configured course workspace into English Markdown files split by printed page number, with an index ready for the Dialogue Unit candidate Skill. Do not use this skill for archive/runtime data."
---

# Textbook Screenshot to Markdown

Use this skill only when the caller has supplied an explicit course workspace. Its input is normally a unit directory under that workspace's textbook-image root, and its output is a sibling `english-text/` directory inside that unit. This is a local content-preparation workflow. It does not read or write archive data, call a provider, use credentials, publish a Dada runtime Skill, or commit changes.

## Workflow

1. Confirm the supplied course workspace and inspect the applicable local governance files before editing. List image files with the workspace's available file-listing tool; do not infer textbook page numbers from camera filenames or file order.
2. View every candidate image at a readable resolution. Record the printed textbook page number, the source image filename, and the visible page boundary. If a photograph contains part of an adjacent page, transcribe only the page whose printed number is the target. If a printed page number cannot be confirmed, stop that page and report it for review instead of inventing one.
3. Create or reuse `<unit>/english-text/`. Use one file per printed page, named `page-<number>.md`, plus `README.md` as the page index. Preserve existing page files unless the user explicitly asks for replacement; update only pages supported by the new screenshots.
4. Transcribe the visible English text, preserving headings, section labels, exercise numbers, dialogue speakers, question wording, tables, legends, examples, captions, footnotes, and printed safety notes. Keep the output Markdown in English. Represent answer lines and empty boxes as blanks or omit them when they contain no printed text.
5. Never silently guess a word, page number, table-cell meaning, or icon-to-subject mapping. For readable uncertainty use `[unclear: ...]`; for an image-only element, describe it briefly only when needed to explain the surrounding text. Keep a short list of unresolved items in `README.md`.
6. Add a relative source-image link to each page file when it is useful for checking, and make the index link every generated page in ascending printed-page order. Do not merge two printed pages into one Markdown file.
7. Validate that every expected page file is non-empty, every index link resolves, page numbers are ordered, and no output was written outside the unit's `english-text/` directory. Return the output path, page range, source-to-page mapping and unresolved-item count as the machine-readable handoff.
8. If the caller also supplied Unit ID, Unit title, target version and allowed source pages, invoke `dada-textbook-markdown-to-unit-candidate` in the same course-maintenance task. It may continue only when the handoff has no unresolved item. If that metadata is absent, report exactly those missing inputs; do not infer a Unit ID, version or page scope.
9. Do not generate targets, Chinese meanings, scenarios, question intents or Review decisions yourself. Do not commit, publish, enable a Unit or send the content elsewhere.

For detailed transcription conventions and the page-file template, read [references/transcription-rules.md](references/transcription-rules.md) before producing or revising the Markdown.
