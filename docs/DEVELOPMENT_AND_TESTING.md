# Development and testing

The public repository exposes one required verification command:

```bash
pnpm run test:offline
```

It runs deterministic Python contracts and workflow tests, generic Node inbound tests, and a captured Review replay. It uses temporary SQLite databases and fake gateways. It must not call a model API, contact an OpenClaw Gateway, send a message, or use a real archive.

## Change discipline

1. Read the affected runtime, contract, Skill, policy, and test code before changing behavior.
2. Keep language decisions in LLM contracts and deterministic effects in program adapters.
3. Add a focused regression for every changed state transition, contract field, queue rule, scheduling rule, or recovery behavior.
4. Run the offline suite and `scripts/check-public-release.sh` before contributing.

## Optional external validation

Model evaluation and live channel verification are intentionally not part of this repository's default command. An operator who adds them must use isolated test data, explicit opt-in configuration, protected credentials, and a separate deployment review. Passing an offline test does not prove a live-channel deployment.
