# AI Models

## Manage models
- `/models` — show configured providers and current roles (executor / reviewer)
- `/models add` — add a new provider

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
