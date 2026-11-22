# Docker Linux host-resource fixture

Qualifies `std:linux`: typed Debian accounts, packages, directories, files and symlinks
applied locally (or over a caller-granted SSH alias) through one helper executable.
Helix owns desired state, derived dependency order, review matching and postconditions;
`host.py` owns one mechanism per `observe`/`apply` call and never plans.

Run (opt-in, Docker authority is never inherited): `./manage.py test --suite docker_linux`
(`HELIX_DOCKER_SUDO=1` for `sudo docker`). `HELIX_DOCKER_LINUX_SCENARIO` selects one scenario or a
comma-separated set of distinct scenario names,
`HELIX_DOCKER_LINUX_REPEAT` repeats, `HELIX_DOCKER_LINUX_KEEP_FAILED=1` retains a failed container.
The harness reuses the nginx suite's Docker driver, pinned base images and cleanup labels
(`./manage.py docker-nginx cleanup RUN_ID`). Evidence: `runtime/docker-linux/RUN_ID/`.

## Resource semantics

| Kind | Key | Fields (defaults) | Prerequisites derived |
|---|---|---|---|
| `group` | name | `gid` | none |
| `user` | name | `uid`, `group`, `home`, `shell` (`/usr/sbin/nologin`) | its group |
| `package` | `MANAGER:NAME` under `packages: {MANAGER: {NAME: ...}}`; only `apt` | exact `version` | none |
| `directory` | path | `owner`/`group` (root), `mode` (0755) | owner, group, declared ancestors |
| `file` | path | `content`, `owner`/`group` (root), `mode` (0644) | owner, group, declared ancestors |
| `symlink` | path | `target` | declared ancestors |

- Any entry may add `after: [kind:key]`; unknown references, cycles and a present resource
  requiring one declared `state: absent` fail before any mutation, including self references
  and invalid prerequisites on absent resources. One path cannot have multiple declared kinds;
  declared files and symlinks cannot be ancestors of other managed paths.
- Omission never deletes. Only `state: absent` removes, in reverse dependency order.
  Path replacement/removal preserves unmanaged siblings (extra files and other processes);
  native account-ID changes can re-own home files as described below.
  An absent resource needs no creation-only fields. Retain fields or explicit `after`
  edges when they supply the dependencies whose removal order matters.
- A failed resource skips exactly its transitive dependents; independent resources still converge.
- Success requires a fresh observation of that resource, so a helper that claims success without
  mutating fails its postcondition. Reviews bind observed state, inputs, target identity and the
  dependency graph.
- Numeric UID/GID changes plan refreshes for dependent named ownership even when the names
  agree with the old observation. Fresh observation after prerequisites skips refreshes already
  performed by the account tool; non-identity account changes do not rewrite unrelated files.
- File contents are exact UTF-8 bytes, including CRLF, CR and NUL. Replacements use a
  staged inode with ownership/mode and bytes flushed before publication; atomic replacement
  preserves another hardlink's old bytes. Symlink ancestors and wrong object kinds fail closed.

## Limits

One pinned Debian container, root helper, fixture-owned path roots (`/etc/helix-demo`,
`/srv/helix`, `/home`, ...); the helper refuses other paths. Packages are exact-version installs
from a pinned snapshot. Changing a group's gid does not re-own files; ownership is by name.
Account tools retain their native Debian semantics; UID changes can re-own files in a user's
home. Caller-granted root commands are not an OS sandbox. Files are bounded to 1 MiB and
helper JSON requests to 8 MiB; runtime work/data budgets and caller command-output/time limits
impose workload-dependent lower bounds. Dependency/context planning excludes file bodies.
The fixture pins exactly one `USERGROUPS_ENAB no` definition in `/etc/login.defs` so
`userdel` cannot delete a separately managed or omitted private group. The helper refuses
user deletion before mutation if that prerequisite becomes unsafe or ambiguous.
Helper-framing acceptance includes a JSON request exceeding Linux's per-argument limit,
through the Helix [`helper.yaml`](../../programs/docker_linux/helper.yaml) test program.
This mechanism test does not qualify the complete provider lifecycle at the same payload size.
A separate 140 KB inline-body capacity probe checks metadata independence and either a
completed plan or an explicit runtime limit with no mutations; it does not qualify
application of that larger combined manifest. `inline-capacity.json` records the outcome.
`std:helper` uses `OP -` with JSON on stdin, avoiding argv size limits for file contents.
The fixture also accepts `OP JSON` for direct mechanism inspection. Nested package-command
output and execution time are bounded independently of the final helper reply.
An apply error may follow visible mutation (`host_error.mutation_may_have_occurred`);
completed resources are not rolled back. Re-observe before a fresh review. There is no
compare-and-swap protection against an external writer racing between observations.
Even failure before file replacement can update the parent directory's mtime when the
staging name is created and removed; preserving the live file does not restore that timestamp.
No services, boot persistence, templates, secrets, firewall, cron, SELinux/ACLs, non-Debian hosts
or fleet scheduling. See `CHARTER.md` section 11 for the roadmap.

## Containers configuring each other

Run the peer acceptance scenario and leave the successful pair running:

```sh
HELIX_DOCKER_SUDO=1 HELIX_DOCKER_LINUX_SCENARIO=peers HELIX_DOCKER_LINUX_KEEP_PEERS=1 \
  ./manage.py test --suite docker_linux
```

Omit `HELIX_DOCKER_SUDO=1` when your account can access the Docker daemon directly.
Omit `HELIX_DOCKER_LINUX_KEEP_PEERS=1` for automatic cleanup. Retention requires
the single `peers` scenario and one repetition. The report prints the run ID;
container names are in `runtime/docker-linux/RUN_ID/peers.json`. The existing
`./manage.py docker-nginx cleanup RUN_ID` command removes the owned pair, network
and image (with the same Docker environment).

Docker bootstraps two containers, Helix, the bounded host helper and fresh SSH
identities. It exchanges public keys and pins host keys on their private bridge;
no SSH port is published, host path mounted or Docker socket exposed. Each
container then runs [`peer.yaml`](../../programs/docker_linux/peer.yaml): Helix
imports the declarative baseline, observes its peer, creates a fresh plan and
explicitly applies it. This is an authorized automatic-convergence workflow;
the separate `operate.yaml` check/apply interface still supports saved reviews.
The controller receives the peer SSH grant and read access to its program directory,
with no local command grant.

Both directions install the declared accounts, exact package versions, directories,
files and link. Independent OS probes check the result. A second run must make
no mutations; corrupting one peer file must repair exactly that resource. The
suite also checks unmanaged files and processes survive. Run a controller again
inside either retained container, substituting the printed names:

```sh
sudo docker exec SOURCE helix perform --allow-stdout \
  --allow-read /opt/helix-programs \
  --allow-ssh peer,host=TARGET,user=root,known_hosts=/run/helix-peer/known_hosts,identity=/run/helix-peer/identity \
  --command-timeout-ms 120000 /opt/helix-programs/docker_linux/peer.yaml
```

The peer scenarios exercise sequential and simultaneous controllers writing to
different targets. They do not coordinate multiple writers to the same target,
restore lost containers, or bootstrap trust from untrusted
machines. Transport bootstrap and independent verification belong to the harness;
desired state, planning, dependency order and application policy belong to Helix.

## Adversarial acceptance

All scenarios run by default when this suite is explicitly selected. Focus and repeat
through the same entrypoint, for example:

```sh
HELIX_DOCKER_SUDO=1 HELIX_DOCKER_LINUX_SCENARIO=peer_failures,peer_recovery,peer_parallel \
  HELIX_DOCKER_LINUX_REPEAT=2 ./manage.py test --suite docker_linux
```

| Scenarios | Independently checked behavior |
|---|---|
| `baseline`, `updates`, `drift`, `removal`, `lifecycle` | Actual accounts/packages/files, targeted changes, no-op inode/mtime preservation, reverse removal, unmanaged state and recreation |
| `invalid_input`, `adversarial_plans`, `stale_review` | Pure rejection with no grants; exact diagnostic kinds; cycles, absent prerequisites, path conflicts and stale data/identity/reviews |
| `failure`, `false_success`, `host_failures` | Injected failures and false success for creation and removal across all six resource kinds, exact failed-dependent gating and successful fresh retries |
| `authority`, `filesystem` | Missing authority, malformed success/failure payloads, missing/extra observations, invalid values, symlink ancestors, wrong objects, invalid UTF-8 and oversized files |
| `exact_files`, `file_failures` | Exact bytes including payloads above argv limits, hardlink isolation, errors before/after replacement, staging cleanup and reconciliation of visible partial mutation |
| `identity_changes` | UID/GID updates converge ownership in one apply, including files outside a user's new home, without rewriting files already rebound by the OS tool |
| `user_removal` | User deletion preserves retained and omitted groups; unsafe native private-group deletion policy is rejected before mutation |
| `contention`, `observations` | Real dpkg lock and account-ID conflicts; 48 concurrent observations publishing one complete identity per first-observation round |
| `sequences`, `sensitivity`, `late_drift` | 36 deterministic transitions (seeds 7, 41, 2026), stale-review injection, oracle corruption checks and final verification rejecting a resource changed after its own postcondition |
| `peers`, `peer_failures`, `peer_parallel` | Both directions, simultaneous controllers, no-op/drift cycles, host-key/auth/grant denial, stale remote reviews, SSH timeout and mutation followed by failure |
| `peer_recovery` | Kill the container-resident controller after remote mutation; refuse uncertain replay; restart the target, independently observe, reject mismatched reconciliation, require fresh grants and resume without repeating the mutation |

The `harness` suite runs pure declaration regressions and helper byte/output/time bounds
without Docker, using only project-local scratch space. Fault pauses have a 120-second
ceiling and remain fixture controls, never desired-state input. Killing an SSH controller
does not fence a remote process: timeout/death tests explicitly quiesce the observed paused
helper before reconciliation. The target restart test explicitly restarts fixture SSH;
it does not establish boot-persistent service management. Journals are copied as artifacts
and inspected through the bounded checkpoint API rather than dumped through command output.

## Qualification evidence

[`qualification.yaml`](qualification.yaml) preserves the original baseline and records
the later hardening qualification: 26 scenarios across the integrated run and corrected
continuation, two repetitions of the high-risk peer scenarios, and a retained hardened pair.
The integrated run's initial directory-mtime assertion failure remains recorded explicitly.
Production source manifests and controller digests agree across those four runs. The record
also includes nine shared regression suites, 210 golden cases, 210 mutation-robustness cases,
the explicit 30-second harness timeout and the unqualified large-inline capacity limit.
Raw reports under `runtime/docker-linux/`
retain pinned versions, image identity, commands, independent observations and
cleanup outcomes; those artifacts are local evidence rather than checked-in files.
