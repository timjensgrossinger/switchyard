# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project uses Semantic
Versioning for public releases.

## [Unreleased]

### Added

- Visual README with architecture, routing, wave, and learning-loop diagrams (`docs/assets/`)
- Reference docs: [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md), [docs/CLI.md](docs/CLI.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

### Changed

- **Rebrand:** TGs-router → **Switchyard** — new install path (`~/.local/lib/switchyard`), MCP name, CLI command (`switchyard`), and env prefix (`SWITCHYARD_*`)
- Public repository: `timjensgrossinger/switchyard`
- `TGSROUTER_TEST_MODE` deprecated in favor of `SWITCHYARD_TEST_MODE` (old name still accepted one beta cycle)

## [1.0.0-beta.1] - 2026-06-10

### Added

- Apache License 2.0 with `NOTICE` for third-party attributions
- `VERSION` file and `shared/version.py` as single source of truth for MCP serverInfo
- Routing eval fixture alignment for low-tier override and urgency scoring behavior
- Deterministic routing eval via default config in `SWITCHYARD_TEST_MODE` (ignores local `config.yaml`)

### Changed

- Public beta release: repository metadata, README status, and license updated for OSS
- Removed internal `.planning/` artifacts from version control
- Hardened `.gitignore` for secrets, keys, and environment files
- Routing eval CI workflow now fails correctly on fixture regressions (`pipefail`)

### Notes

- MCP tool schemas may change between beta releases; pin a git tag for stability
- See [KNOWN_BOTTLENECKS.md](KNOWN_BOTTLENECKS.md) for documented performance limits

## [v3.2.0-alpha.1] - 2026-06-08

### Added

- Explicit provider auto-route tier policies preserved through live catalog
  refresh.
- Persisted learning audit-log inspection with filtering and secret redaction.
- Required verify-gate failure semantics and per-signal timeouts.
- Explicit subtask lifecycle states and pre-PID cancellation.
- Public security, contribution, configuration, and CI documentation.
- MIT license, SECURITY.md, CONTRIBUTING.md.
- GitHub Actions CI: Python 3.10–3.13 matrix, ShellCheck, Gitleaks, archive
  inspection, and installer smoke tests.
- Managed uninstaller (`uninstall.sh`).
- Release docs: ARCHITECTURE.md, BENCHMARKS.md, DEMO.md,
  PROVIDER_COMPATIBILITY.md, ROUTING_QUALITY.md, RELEASE_LIMITATIONS.md.

### Fixed

- OpenCode and Junie no longer gain unintended routing tiers after discovery.
- Provider startup now shares the task execution deadline.
- Post-registration early returns no longer leave active subtasks orphaned.
- Patch mode now validates its target path.
- Rewrite length-guard rejection no longer calls a missing database method.
- Concurrent SQLite schema initialization is now race-free.
- Claude model IDs updated to stable `haiku`/`sonnet`/`opus` aliases.
- Claude auth preflight uses `claude auth status`; quarantine clears on
  fresh successful probe.

### Security

- Routing eval accuracy: 100% on 32 fixtures (2 intentional boundary skips).
- Verify gate: missing required tools now fail explicitly (no silent pass).
- Archive: 559 entries, no secrets, runtime state, or generated files.

## [1.9] - 2026-06-08

- Last internal milestone before the public release hardening cycle.

[Unreleased]: https://github.com/timjensgrossinger/switchyard/compare/v1.0.0-beta.1...HEAD
[1.0.0-beta.1]: https://github.com/timjensgrossinger/switchyard/releases/tag/v1.0.0-beta.1
[v3.2.0-alpha.1]: https://github.com/timjensgrossinger/switchyard/compare/v1.9...v3.2.0-alpha.1
[1.9]: https://github.com/timjensgrossinger/switchyard/releases/tag/v1.9
