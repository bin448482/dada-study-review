# Optional TTS

TTS is an optional outbound enhancement. The offline core does not require a provider, a key, network access, or an audio file.

## Configuration

Copy `config/examples/review-tts-config.json.example` outside the repository and replace its example values. Keep the API key only in the process environment:

```bash
export DADA_REVIEW_TTS_API_KEY="your-provider-key"
```

The public configuration contract supports the fixed `minimax` and `volcengine_seed` adapters. It does not accept arbitrary endpoints, headers, request bodies, model controls, or file paths. Set `enabled` to `false` for the normal no-network configuration.

## Fake validation

The default offline suite uses fake transports and verifies request shape, MP3 bounds, and text fallback without contacting a provider:

```bash
pnpm run test:offline
```

## Explicit live smoke

Live testing is opt-in, costs provider quota, and must use only the fixed public sentence. It must never use archive text or a real child message:

```bash
export DADA_REVIEW_TTS_API_KEY="your-provider-key"
python scripts/run-tts-smoke.py   --provider minimax   --config /path/to/review-tts-config.json   --text "Hello. This is a public TTS smoke test."
```

The command prints only provider, format, and byte count. It does not write SQLite, checkpoints, archive files, outbox media, logs containing credentials, or channel messages. A live smoke result does not prove OpenClaw or a real channel deployment.

TTS failure must remain a text-only fallback and must not change workflow state, queue order, assessment, scheduling, or checkpoint facts.
