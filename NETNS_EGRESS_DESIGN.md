# Design spike: bubblewrap netns egress scoping (Linux parity with macOS)

**Status:** not implemented — a research spike, written up so a contributor
with real Linux hardware can pick it up with a head start. See
[ROADMAP.md](ROADMAP.md) / [README.md](README.md#roadmap) for where this sits.

## The gap today

`--executor-network <hosts>` records the declared allowlist for the audit
trail on every platform, but only **enforces** it on macOS. On Linux/bwrap,
network is currently all-or-nothing: `--share-net` gives the sandboxed
process the full host network, `--unshare-all` (the default) gives it none.
There's no current middle ground.

## Why this is genuinely harder than it looks

macOS's enforcement (`loophole/sandbox.py::_seatbelt_profile`,
`loophole/egress_proxy.py`) works because Seatbelt has a **declarative
primitive for this exact case**: `(allow network-outbound (remote ip
"localhost:*"))`. The jail can reach 127.0.0.1 and nothing else; a proxy
process running outside the jail, on the host's loopback, forwards to the
real allowlisted hosts via `CONNECT` tunneling (advertised to the jail via
`HTTP_PROXY`/`HTTPS_PROXY`).

bubblewrap has no equivalent primitive. `--unshare-net` gives the sandboxed
process a **fresh, isolated network namespace** — critically, a namespace's
loopback (`127.0.0.1`) is private to that namespace. A process bound to
`127.0.0.1:PORT` in the host's default namespace is **not reachable** from
inside a bare unshared child namespace at all. Getting to "jail can reach a
host-side proxy and nothing else" needs real user-mode networking, not just
namespace flags.

## The proposed architecture

[`slirp4netns`](https://github.com/rootless-containers/slirp4netns) — the
same unprivileged user-mode networking rootless Podman/Docker use for
networking without root — is the right building block:

- By default, **a service bound to 127.0.0.1 on the host is reachable from
  inside the namespace via the slirp4netns gateway address (default
  `10.0.2.2`)** ([slirp4netns.1.md](https://github.com/rootless-containers/slirp4netns/blob/master/slirp4netns.1.md)).
  That's exactly the primitive needed: point the egress proxy at the
  gateway address instead of `HTTP_PROXY=http://127.0.0.1:<port>`.
- slirp4netns has **no flag to restrict general outbound internet access** —
  by design it gives real internet connectivity. Getting to "loopback (via
  gateway) only, nothing else" needs an `iptables`/`nft` rule applied
  **inside** the sandboxed network namespace, e.g. (illustrative, not yet
  tested):
  ```
  iptables -A OUTPUT -d 10.0.2.2 -j ACCEPT   # the gateway / host-loopback path
  iptables -A OUTPUT -j DROP                  # everything else
  ```
  This is possible unprivileged because creating your own new user+net
  namespace grants full capabilities (including `CAP_NET_ADMIN`) *within*
  that namespace — the same mechanism rootless containers rely on.
- `--disable-host-loopback` and `--disable-dns` are relevant knobs to
  revisit once this is wired up (the DNS resolver at `10.0.2.3` can itself
  leak host-loopback access per slirp4netns's own docs — worth an explicit
  decision, not an oversight).

## The real engineering problem: synchronization, not networking

slirp4netns attaches to an **existing** process's namespace by PID
(`slirp4netns [OPTION]... PID|PATH [TAPNAME]`) — it doesn't create the
namespace itself. That means the sequence has to be:

1. Start bwrap with `--unshare-net`, but **block the sandboxed command from
   actually running yet**.
2. From a sibling host process: get bwrap's child PID, attach slirp4netns to
   its namespace, then `nsenter --net=/proc/<pid>/ns/net` to apply the
   iptables DROP-by-default rule *inside* that namespace.
3. Only then let the sandboxed command actually start.

bwrap has exactly the right primitives for this handshake, already used by
Flatpak for similar setup-before-exec needs:
- `--sync-fd FD`: bwrap blocks reading FD before running the command, so an
  external process controls exactly when it starts.
- `--info-fd FD`: bwrap writes the child's PID (JSON, one object per line)
  once the child has started — but this fires *after* the child process
  already exists, which is what makes `--sync-fd` necessary: without it,
  there's a real race where the sandboxed command could reach the network
  before slirp4netns/iptables are attached.

The correct flow: `--info-fd` to learn the PID as early as possible,
`--sync-fd` to hold the command at the starting line until the *loophole*
side has attached slirp4netns and applied the iptables rule inside that
namespace, then release the sync-fd.

None of this has been implemented or tested against a real kernel — it's a
paper design from reading slirp4netns's and bwrap's own documentation
closely, not a live-verified mechanism (unlike everything else this session
touched, which was checked against a real binary before being trusted).
Sources: [slirp4netns docs](https://github.com/rootless-containers/slirp4netns/blob/master/slirp4netns.1.md),
[bwrap manpage](https://manpages.debian.org/testing/bubblewrap/bwrap.1.en.html).

## What a real test would need to prove

Mirroring `tests/test_sandbox.py`'s existing pattern (real escape attempts,
not mocks): a sandboxed command that tries to reach a non-allowlisted host
must actually fail to connect (not just "the proxy would have blocked it in
theory"), and a request to the egress proxy via the gateway address must
actually succeed and reach the real allowlisted host. Both need to run
under real bwrap + slirp4netns on a real Linux kernel — this can only be
verified in CI (`sandbox.yml`), the same constraint that shaped the
mutation-testing verifier's Seatbelt/PTY gap earlier this session.

## Why this wasn't implemented directly

Getting namespace/process synchronization wrong is easy to get subtly
wrong in a way that *looks* correct (tests pass) while a real gap remains
(e.g., a timing window before the iptables rule lands) — worse than the
current honest state, where the gap is at least documented and audited
rather than silently assumed enforced. This needs real hands-on iteration
against actual Linux network namespaces, which isn't available in this
session (macOS-only dev environment); blind iteration against CI logs
alone is a poor way to get security-critical sandbox code right. Flagged
here for a contributor with direct Linux access — see
[CONTRIBUTING.md](CONTRIBUTING.md).
