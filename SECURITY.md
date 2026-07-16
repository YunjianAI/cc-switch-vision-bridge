# Security policy

Do not include API keys, Claude profiles, CC Switch databases, logs, cached image descriptions, or conversation transcripts in bug reports.

Report suspected credential exposure privately to the repository owner. Revoke the affected provider key before collecting additional diagnostics.

The bridge sends intercepted images to the configured third-party vision provider. Review that provider's data handling policy before use.

MCP configuration backups are kept under `%LOCALAPPDATA%\CCSwitchVisionBridge\backups`, not beside a project configuration file. A backup preserves the original file exactly and can therefore contain credentials that were already stored there; protect it as sensitive local data.
