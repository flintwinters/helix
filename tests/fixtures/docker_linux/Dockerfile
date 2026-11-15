# Required pinned inputs come from the nginx fixture's versions.json through the project harness.
ARG HELIX_BUILDER_IMAGE
ARG HELIX_RUNTIME_IMAGE
FROM ${HELIX_BUILDER_IMAGE} AS build
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY std ./std
COPY completions ./completions
COPY editor/helix.schema.json ./editor/helix.schema.json
RUN cargo build --locked --release

FROM ${HELIX_RUNTIME_IMAGE}
ARG HELIX_PACKAGE_SNAPSHOT
# No package refresh is needed during resource application. The managed packages stay absent.
RUN test -n "$HELIX_PACKAGE_SNAPSHOT" && rm -f /etc/apt/sources.list.d/* && \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s bookworm main\n' "$HELIX_PACKAGE_SNAPSHOT" > /etc/apt/sources.list && \
    printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod 755 /usr/sbin/policy-rc.d && \
    apt-get -o Acquire::Check-Valid-Until=false update && \
    apt-get install -y --no-install-recommends python3 ca-certificates openssh-client openssh-server && \
    test ! -e /usr/bin/jq && test ! -e /usr/bin/tree
# Account and group resources are independent: userdel must not implicitly delete groups.
RUN sed -i -E '/^[[:space:]]*USERGROUPS_ENAB([[:space:]]|$)/d' /etc/login.defs && \
    printf '\nUSERGROUPS_ENAB no\n' >> /etc/login.defs
COPY --from=build /build/target/release/helix /usr/local/bin/helix
COPY tests/fixtures/docker_linux /opt/helix-linux-fixture
COPY tests/programs /opt/helix-programs
RUN ln -s /opt/helix-linux-fixture/host.py /usr/local/bin/helix-linux-host && \
    chmod 755 /opt/helix-linux-fixture/host.py && \
    mkdir -p /var/lib/helix-linux-host /run/helix-linux /var/lib/helix-unmanaged && \
    printf 'unmanaged fixture sentinel\n' > /var/lib/helix-unmanaged/sentinel
LABEL org.helix.fixture="docker_linux" org.helix.fixture.version="1"
WORKDIR /opt/helix-programs
# The container is started with --init, which reaps children; no service supervisor is claimed.
ENTRYPOINT ["sleep", "infinity"]
