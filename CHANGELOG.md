# Changelog

## 0.1.2-dev

- Bind each image to the question from its own conversation turn.
- Reanalyze the most recent image group only when a text-only follow-up asks a new question.
- Reuse historical image descriptions when a new turn contains a different image, avoiding repeated MiMo calls as image history grows.
- Treat `timeout_seconds` as one total budget shared by all vision retry attempts, preventing a nominal 60-second timeout from blocking for roughly 120 seconds.
- Return locally generated failures in the Anthropic API error shape so Claude clients can surface preprocessing errors and request IDs.

## 0.1.1-beta

- Use MiMo's official `api-key` authentication and `max_completion_tokens` request field.
- Bound vision output and retry transient failures to reduce screenshot timeouts.
- Include the user's question in the effective prompt and cache key.
- Remove expired cache files and reject empty image payloads safely.
- Make repeated installation recognize and replace the project's own running instance.
- Restore only the MCP entry owned by the project during uninstall.
- Suppress `mcp-vision` debug request logging so image base64 is not written to stderr logs.
- Keep MCP backups under LocalAppData instead of leaving potentially sensitive copies in a repository.

## 0.1.0-beta

- Intercept user-pasted Anthropic image blocks before text-only models receive them.
- Recursively replace images nested in `tool_result` blocks from screenshot tools.
- Use a configurable OpenAI-compatible vision provider with deterministic descriptions.
- Cache by image, model, prompt version and effective prompt hash without storing image data.
- Keep direct-image failures closed while degrading tool-image failures to explicit text.
- Preserve ordinary JSON, streaming responses and CC Switch authentication headers.
- Add Windows Credential Manager storage, profile guarding, logon startup and safe uninstall.
- Reuse `mcp-vision` for local paths and image URLs.
