# Security policy

## Supported versions

| Version | Security fixes |
|---|---|
| 0.4.x | Supported |
| 0.3.x and older | Upgrade to the latest release first |

## Reporting a vulnerability

As of 2026-07-15, this repository does not have GitHub private vulnerability
reporting enabled. Do not publish exploit details, tokens, prompts, model
outputs, local paths, or other sensitive evidence in a public issue.

Open a minimal [security coordination issue](https://github.com/Seraphim0916/omnilane/issues/new)
that contains only:

- the affected released version;
- the broad component, such as installer, dispatcher, jobs CLI, or Live UI;
- a request for a private contact channel.

Wait for the maintainer to establish a private channel before sharing the
reproduction or impact details. Maintainers should enable GitHub private
vulnerability reporting when a stable private disclosure workflow is ready.

Useful reports include a minimal reproduction, expected and actual behavior,
impact, platform, and a proposed mitigation if available. Remove credentials,
cookies, provider responses, and proprietary source code.

## Security-relevant scope

Examples include shell or routing injection, path traversal, unsafe installer
replacement, cross-user prompt or result disclosure, loopback authentication
bypass, unsafe symlink handling, and worker processes that survive a declared
timeout. Model answer quality and upstream provider availability are normally
not security vulnerabilities in Omnilane itself.
