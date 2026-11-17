ARG BASE
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server iproute2 util-linux mawk ca-certificates && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd /root/.ssh /var/lib/helix-facts
COPY authorized_keys /root/.ssh/authorized_keys
RUN chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys && ssh-keygen -A && printf 'old' > /var/lib/helix-facts/value && printf '{"version":1,"value":10}' > /var/lib/helix-facts/observation.json
COPY helix /usr/local/bin/helix
CMD ["/usr/sbin/sshd", "-D", "-e", "-o", "PasswordAuthentication=no"]
