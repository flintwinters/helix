# Required pinned inputs come from versions.json through the project harness.
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
# No package refresh is needed during resource application. nginx remains absent.
RUN test -n "$HELIX_PACKAGE_SNAPSHOT" && rm -f /etc/apt/sources.list.d/* && \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s bookworm main\n' "$HELIX_PACKAGE_SNAPSHOT" > /etc/apt/sources.list && \
    printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod 755 /usr/sbin/policy-rc.d && \
    apt-get -o Acquire::Check-Valid-Until=false update && \
    apt-get install -y --no-install-recommends python3 ca-certificates && \
    test ! -e /usr/sbin/nginx
COPY --from=build /build/target/release/helix /usr/local/bin/helix
COPY tests/fixtures/docker_nginx /opt/helix-fixture
COPY tests/programs /opt/helix-programs
RUN ln -s /opt/helix-fixture/host.py /usr/local/bin/helix-nginx-host && \
    chmod 755 /opt/helix-fixture/host.py && \
    mkdir -p /var/lib/helix-nginx-host /run/helix-nginx && \
    printf 'unmanaged fixture sentinel\n' > /var/lib/helix-nginx-host/sentinel.txt
LABEL org.helix.fixture="docker_nginx" org.helix.fixture.version="1"
WORKDIR /opt/helix-programs
ENTRYPOINT ["python3", "/opt/helix-fixture/supervisor.py"]
