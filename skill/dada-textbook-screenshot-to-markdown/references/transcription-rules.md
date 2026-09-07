# Transcription rules

## Source and page boundaries

- The printed number on the textbook page is authoritative.
- A camera filename is source metadata only.
- A spread photograph may show a sliver of the next page. Do not copy that sliver into the current page file.
- If two photographs show the same printed page, keep the clearest source and record the duplicate in the index notes.
- If a page number is cropped, blurred, or covered, use `[page number unclear]` in the working notes and ask for confirmation before naming the file.

## What to preserve

Preserve the printed English that affects study or review:

- unit, lesson, section, and exercise headings;
- instructions and questions;
- dialogue speaker names and spoken lines;
- reading passages and online posts;
- vocabulary lists, word boxes, tables, legends, examples, and captions;
- printed footnotes, glossary notes, and safety guidance.

Do not create answers for an exercise. Keep a printed example answer, but leave student answer areas blank. A blank can be represented as `____________________` or an empty table cell.

## Tables and visual elements

Use a Markdown table when the printed words and cell relationships are clear. For a pictorial timetable, word web, or poster:

- transcribe all readable labels and surrounding instructions;
- retain the named categories or legend;
- do not infer a subject, word, or value from an ambiguous icon;
- note the unresolved mapping in the page file or `README.md`.

## Uncertainty

Use one of these explicit forms rather than silently correcting the source:

- `[unclear: word]` when the location is known but the letters are uncertain;
- `[illegible]` when the printed text cannot be read;
- `[image-only: ...]` for a meaningful visual item with no printed wording;
- `[page number unclear]` when the page boundary cannot be established.

Keep punctuation, capitalization, contractions, names, times, and spelling as printed unless the user asks for editorial correction. If a likely typo is visible, preserve it and mention it in the review notes.

## Page-file template

```markdown
# Page <number> — <short title>

_Source image: [filename.jpg](../filename.jpg)_

## <section>

<transcribed English text>
```

The `README.md` should contain an ascending page index, the covered page range, source-image mapping when filenames are non-obvious, and a short “Review notes” section listing uncertainty or intentionally summarized visual content.
