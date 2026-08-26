# Architecture decisions

## Deterministic state, model language decisions

The program owns authorization, state transitions, short transactions, queue order, question locks, scheduling, archival, and recovery. LLM definitions own English semantics and feedback. This preserves deterministic, auditable business facts without trying to replace language understanding with string rules.

## One authoritative archive per deployment

Entry and Review use one SQLite business store. Exact externally supplied scope identifiers isolate workflow facts. The repository exposes fixed actions rather than generic mutation SQL.

## Freeze the Review round

At explicit Review start, due items are snapshotted in order. This keeps the round stable and prevents both newly due items and LLM output from changing its membership or order.

## Commit a question before delivery

The prompt and visible reply are committed with the question lock. If a process stops after commit, recovery uses the already committed delivery; it does not create a new question or duplicate schedule effects.

## Static identity and minimal integration permissions

The generic inbound plugin accepts only a configured exact identity and fixed workflow actions. It must not infer identity from natural language or expose shell, file-system, browser, or generic database tools.

## Separate definition from transport

`skill/` is the editable LLM behavior source. The production composition packages verify a reviewed definition digest and receive transport credentials only at process startup. Core Graph and repository packages do not import provider SDKs or retain secrets.
