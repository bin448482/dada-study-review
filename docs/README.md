# Documentation

This documentation describes the reusable reference implementation. It deliberately omits household deployment details, real identities, archives, credentials, message-delivery targets, and operational history.

1. [Project overview](PROJECT_OVERVIEW.md)
2. [Architecture](ARCHITECTURE.md)
3. [Domain and state model](DOMAIN_AND_STATE_MODEL.md)
4. [Data and contract reference](DATA_AND_CONTRACT_REFERENCE.md)
5. [Architecture decisions](ARCHITECTURE_DECISIONS.md)
6. [Development and testing](DEVELOPMENT_AND_TESTING.md)
7. [OpenClaw integration](OPENCLAW_INTEGRATION.md)
8. [Optional TTS](TTS.md)

The public runtime also includes the Dialogue workflow and its minimal versioned Unit package. Textbook screenshots, transcription inputs, candidate-generation history, and household curriculum data are not included.

The included OpenClaw material is a generic integration reference, not a deployment recipe. Operators must supply their own static authorization mapping, archive location, model configuration, and secret handling.
