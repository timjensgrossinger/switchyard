# Release Limitations and Roadmap

## Known Alpha Limitations

- Windows is not supported by the installer or process-control helpers.
- Provider behavior depends on locally installed CLI versions and account
  entitlements.
- Live provider smoke tests are machine-specific and remain a release gate.
- MCP transport-disconnect cleanup still needs an explicit regression test.
- Branch protection and final release archive inspection are repository-hosting
  operations and cannot be completed from the local source tree alone.
- OpenCode defaults to low-only routing.
- Junie defaults to medium-only routing.
- Windsurf is detection-only.

## Comparison Boundaries

Switchyard is not positioned as a replacement for a specific AI coding tool.
It is a local routing and orchestration layer for operators who already use one
or more AI CLIs. Comparisons should be limited to observable behavior:

- Local-first MCP routing across installed CLIs.
- Cost-aware tier selection.
- Provider diagnostics and explicit readiness reasons.
- Approval-gated learned agents.
- Workspace write auditing and preview handling.

Avoid unsupported market claims, provider quality rankings, or claims about
private provider quotas that are not available through stable APIs.

## Privacy Model

- Switchyard does not require central service credentials.
- Provider CLIs receive the prompts they are asked to execute.
- Local telemetry, routing history, learning state, and caches stay in SQLite
  unless the operator exports or prompts with them.
- Secret fields are redacted from public audit surfaces where structured data
  is returned.

## Cost-Routing Assumptions

Cost rank is a routing hint, not a bill estimator. It combines bundled defaults,
provider metadata, and operator overrides. Subscription status and provider
quota windows can change independently from Switchyard.

## Roadmap

- Add explicit MCP transport-disconnect cancellation tests.
- Complete live smoke matrix for supported providers.
- Add Linux and macOS clean install/reinstall/uninstall CI jobs with real shell
  environments.
- Add a managed command for project-local OpenCode deregistration guidance.
- Publish versioned release archives after archive inspection and secret scans.
