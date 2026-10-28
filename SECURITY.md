# Security

## Supported version

The `main` branch is the supported version. Fixes are not backported to older
commits.

## Reporting a vulnerability

Use GitHub's **private vulnerability reporting** on this repository
(Security → Report a vulnerability). Do not open a public issue.

Include:

- what the issue lets an attacker do, and from where (the desk, the server, a
  player's machine, the network);
- the version or commit;
- the smallest reproduction you have;
- whether a live server is affected.

## Scope

The suite has four security boundaries:

- **The desk daemon** binds loopback by default. `camelot serve` and
  `camelot studio` are not authentication servers; do not expose them to the
  network.
- **The Console** is the server's control surface. Beyond loopback it requires
  the operator password. Its action forms carry a per-process token.
- **The deploy branch** is the transport between the desk and the server. The
  deploy key is read-only on the project repository; the release key signs the
  release record, and the Console checks it when configured.
- **The players' launcher** updates the client from the feed. It verifies the
  patch list and each file's hash; a shard can also sign the release record.

A report that only shows a local operator can do what the tool documents is not
a vulnerability.

## What not to include

Never put a live secret, a private key, a password, or a player's personal data
in an issue, a pull request, or a test fixture.
