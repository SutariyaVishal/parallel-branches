# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue: use GitHub's **Report a vulnerability** button on the Security tab of this repository (private vulnerability reporting). Include what you did, what you expected, and what happened, and the version from `CHANGELOG.md`.

I aim to reply within a week. This is a small project maintained in spare time, so please allow for that.

## What counts

Most interesting for this project:

- `pb.py` stopping a process it did not start, or deleting files outside the worktrees and profile directories it created.
- A way to make `pb.py` run a command the user did not approve when run on an untrusted repository, beyond the documented fact that commands from `.parallel-branches.json` and `package.json` run in a shell.
- The dev-mode browser using anything other than its own throwaway profile, or opening a non-`localhost` URL.
- State, lock or log files written where another local user can tamper with them.

## Known by design

These are documented in [`skills/parallel-branches/references/safety.md`](skills/parallel-branches/references/safety.md) and are not vulnerabilities:

- Start, install and environment commands from the repository run in a shell on your machine. Review them (`pb up --dry-run`) before running a repository you do not trust.
- The dev-mode browser has web security off. Use it only for the app it opened.
- Its DevTools port listens on `127.0.0.1` and accepts any local process.
