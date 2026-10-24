# syntax=docker/dockerfile:1
# Another machine reached by SSH (step 15, docs/ARCHITECTURE.md, Several machines), for the proof of
# the app's tunnel (tests/docker/ssh-tunnel.sh): Atlas's own image, always serving on its loopback
# as an always-on home does, with sshd beside it and a site up on its loopback for the preview.
ARG BASE=atlas:local
FROM ${BASE}

USER root
# A key only: no password, no root, local forwarding and nothing else of the kind. `*` leaves the
# user's password unusable without locking the account, which sshd would refuse.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends openssh-server >/dev/null \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd && ssh-keygen -A \
    && usermod -s /bin/sh -p '*' atlas
COPY sshd-entry.sh /usr/local/bin/sshd-entry
RUN chmod 755 /usr/local/bin/sshd-entry

EXPOSE 2222
HEALTHCHECK NONE
ENTRYPOINT ["tini", "-g", "--"]
CMD ["sshd-entry"]
