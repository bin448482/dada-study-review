# OpenClaw integration

`runtime/inbound_v3_workflow_hook/` is a generic OpenClaw plugin source. It provides an exact-static-session archive tool for explicit Entry and Review starts, routes active workflows to the corresponding production CLI, and releases the binding after the workflow becomes inactive.

## Required operator inputs

Copy the shape-only example in `config/examples/openclaw-plugin-config.json.example` to a protected deployment configuration. Supply your own:

- exact static identity and inbound conversation mapping;
- archive directory outside the repository;
- Python runtime and definition directories;
- reviewed definition digests;
- provider, model, HTTPS endpoint, and API style;
- protected model credentials through the deployment secret mechanism.

The plugin schema intentionally does not contain an API-key field. The integration process injects the required credential into the appropriate production CLI environment only at execution time.

## Deployment safety

Do not use example values in a live deployment. Do not configure broad channel authorization, arbitrary sessions, generic file access, shell access, browser automation, or dynamic SQL actions. After changing a state-machine definition, calculate its digest with `scripts/definition_digest.py`, update only the corresponding integration configuration, and test with isolated data before any live-channel use.
