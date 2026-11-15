# Docker Linux host-resource fixture

Qualifies `std:linux`: typed Debian accounts, packages, directories, files and symlinks
applied locally (or over a caller-granted SSH alias) through one helper executable.
Helix owns desired state, derived dependency order, review matching and postconditions;
`host.py` owns one mechanism per `observe`/`apply` call and never plans.

Run (opt-in, Docker authority is never inherited): `./manage.py test --suite docker_linux`
(`HELIX_DOCKER_SUDO=1` for `sudo docker`). `HELIX_DOCKER_LINUX_SCENARIO` selects one scenario,
`HELIX_DOCKER_LINUX_REPEAT` repeats, `HELIX_DOCKER_LINUX_KEEP_FAILED=1` retains a failed container.
The harness reuses the nginx suite's Docker driver, pinned base images and cleanup labels
(`./manage.py docker-nginx cleanup RUN_ID`). Evidence: `runtime/docker-linux/RUN_ID/`.

## Resource semantics

| Kind | Key | Fields (defaults) | Prerequisites derived |
|---|---|---|---|
| `group` | name | `gid` | none |
| `user` | name | `uid`, `group`, `home`, `shell` (`/usr/sbin/nologin`) | its group |
| `package` | name | exact `version` | none |
| `directory` | path | `owner`/`group` (root), `mode` (0755) | owner, group, declared ancestors |
| `file` | path | `content`, `owner`/`group` (root), `mode` (0644) | owner, group, declared ancestors |
| `symlink` | path | `target` | declared ancestors |

- Any entry may add `after: [kind:key]`; unknown references, cycles and a present resource
  requiring one declared `state: absent` fail before any mutation.
- Omission never deletes. Only `state: absent` removes, in reverse dependency order.
  Unmanaged siblings (extra files in a managed directory, other processes) are preserved.
- A failed resource skips exactly its transitive dependents; independent resources still converge.
- Success requires a fresh observation of that resource, so a helper that claims success without
  mutating fails its postcondition. Reviews bind observed state, inputs, target identity and the
  dependency graph.

## Limits

One pinned Debian container, root helper, fixture-owned path roots (`/etc/helix-demo`,
`/srv/helix`, `/home`, ...); the helper refuses other paths. Packages are exact-version installs
from a pinned snapshot. Changing a group's gid does not re-own files; ownership is by name.
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

This qualifies two sequential, independent controllers. It does not coordinate
concurrent writers, restore lost containers, or bootstrap trust from untrusted
machines. Transport bootstrap and independent verification belong to the harness;
desired state, planning, dependency order and application policy belong to Helix.
