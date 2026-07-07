# Security Policy

## Reporting a Vulnerability

Please report suspected security vulnerabilities in `Chuzom/loophole` privately using GitHub private security advisories:

https://github.com/Chuzom/loophole/security/advisories/new

Do **not** report security vulnerabilities as public GitHub issues, pull requests, discussions, or comments.

When reporting, please include:

- A description of the vulnerability and its impact
- Steps to reproduce or a proof of concept, if available
- Affected versions, commits, configurations, or platforms
- Any known mitigations or workarounds
- Whether the issue is being actively exploited, if known

We aim to acknowledge valid reports within **3 business days**. After acknowledgement, we will investigate, coordinate a fix, and communicate expected remediation and disclosure timelines through the private advisory.

## Scope

Security issues in scope include vulnerabilities affecting the security boundary of `loophole`, including but not limited to:

- OS sandbox and verifier boundary issues
  - Sandbox escapes from macOS Seatbelt or Linux bubblewrap isolation
  - Leakage of host secrets, credentials, environment variables, files, or other sensitive data into sandboxed AI-generated code
  - Bypasses of filesystem write allowlists or other sandbox policy restrictions
  - Incorrect verifier behavior that allows untrusted AI-generated code to affect host state outside the intended boundary

- GitHub Action security issues
  - Injection vulnerabilities in `action.yml` or related workflow execution paths
  - Unsafe handling of workflow inputs, repository data, environment variables, tokens, or generated code
  - Behavior that could allow privilege escalation, secret exposure, or unintended command execution in CI

General bugs, feature requests, documentation issues, and non-security reliability problems should be reported through the normal public issue tracker.

## Supported Versions

Security fixes are provided for the latest released version of `loophole` on PyPI and the current `main` branch of `Chuzom/loophole`.

Older releases may not receive security patches. Users are encouraged to upgrade to the latest available release.

## Disclosure Process

We request coordinated disclosure to protect users while a fix is prepared.

Once a vulnerability is confirmed, maintainers will:

- Assess severity and affected versions
- Develop and test a fix
- Release a patched version when appropriate
- Publish an advisory or release note after remediation, crediting the reporter if desired

Please do not publicly disclose the vulnerability until maintainers have had a reasonable opportunity to investigate and release a fix.
