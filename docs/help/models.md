# AI Models

## Manage models
- `/models` — show configured providers and current roles (executor / reviewer)
- `/models add` — add a new provider
- `/models check [openrouter]` — verify key validity and print usage/balance metadata

## Providers
- **OpenRouter** (recommended) — one API key gives you Claude, GPT, Gemini, DeepSeek, Llama, etc.
- **Anthropic** — direct Claude access
- **OpenAI** — direct GPT access
- **Gemini** — direct Google access
- **Ollama** — local models (no API key, runs on your machine)

## Roles
- **Executor** (Model A) — generates SQL and explanations
- **Reviewer** (Model B) — checks SQL safety in `secure` and `secure-auto` modes

You can mix providers: e.g., Claude as executor + GPT as reviewer via OpenRouter.

## API keys
Stored in `~/.payp/models.toml` with chmod 600. Never committed to git.

## Key health check
`/models check` calls OpenRouter read-only endpoints:
- `/api/v1/key` — key validity, limits, usage
- `/api/v1/credits` — account credits/usage (may require a management key)

No model inference request is made, so this does not spend tokens.
The same validation is used during `/models add` for OpenRouter keys.
If the key is invalid/unavailable, payp asks whether to enter a new key (`y/n`).
