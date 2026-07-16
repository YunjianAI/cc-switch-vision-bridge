# Changelog

## 0.1.0-beta

- Intercept user-pasted Anthropic image blocks before text-only models receive them.
- Recursively replace images nested in `tool_result` blocks from screenshot tools.
- Use a configurable OpenAI-compatible vision provider with deterministic descriptions.
- Cache by image, model, prompt version and effective prompt hash without storing image data.
- Keep direct-image failures closed while degrading tool-image failures to explicit text.
- Preserve ordinary JSON, streaming responses and CC Switch authentication headers.
- Add Windows Credential Manager storage, profile guarding, logon startup and safe uninstall.
- Reuse `mcp-vision` for local paths and image URLs.
