# Security

claude-safe runs Claude Code inside a throwaway Docker container. This page says
what that protects against, what it does not, and how to report a problem.

## Threat model

The container defends the host. Nothing on the host is reachable from inside it
except the folder you explicitly mount. The container is removed when the session
ends (`--rm`).

It does not defend against:

- Anything you mount. A mounted folder is writable by Claude, and the container
  cannot protect its contents.
- Network access. The container can reach the network, so anything sent out of it
  can leave the machine.
- A compromised image or base layer. Review what you build and pull.
- Kernel or Docker escapes. The container is a strong boundary, not a hardened VM.

## Credentials

Only the tokens you pass into the container enter it. Do not mount your home
directory or your SSH keys. Prefer short-lived tokens with the smallest scope that
works. Never commit a token to the repository, to a log or to a report.

## Auto mode

Auto mode lets Claude run tool calls without asking you each time. Inside the
container it still has only what you mounted and passed in. Use it only for work
you would accept without reading each step.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it through GitHub
private vulnerability reporting (a GitHub security advisory) on this repository.
That keeps the details private until a fix is ready.

Say what you found, how to reproduce it, and which version you tested. We aim to
acknowledge a report within a few working days, then agree on a fix and a
disclosure date with you.

## Supported versions

Only the latest release on the `master` branch receives security fixes. Older
releases are not patched; upgrade to the latest version.
