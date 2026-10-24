#!/bin/sh
# The SSH machine of tests/docker/ssh-tunnel.sh: the key it takes ($AUTHORIZED_KEY), sshd on 2222,
# a site on the loopback's port 3000, and Atlas on the loopback's 18790 with $ATLAS_SERVER_TOKEN.
set -eu
mkdir -p /data/home/.ssh /data/www
printf '%s\n' "$AUTHORIZED_KEY" > /data/home/.ssh/authorized_keys
chown -R atlas /data/home /data/www
chmod 700 /data/home/.ssh
chmod 600 /data/home/.ssh/authorized_keys
echo "a site up on the other machine" > /data/www/hello.txt

/usr/sbin/sshd -e -p 2222 \
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o PermitRootLogin=no \
  -o AllowTcpForwarding=local -o X11Forwarding=no

su -s /bin/sh atlas -c "cd /data/www && exec /app/.venv/bin/python -m http.server 3000 --bind 127.0.0.1" \
  >/dev/null 2>&1 &

exec su -s /bin/sh atlas -c "ATLAS_HOME=/data HOME=/data/home ATLAS_LOG_FILE=false \
  ATLAS_SERVER_TOKEN='$ATLAS_SERVER_TOKEN' exec /app/.venv/bin/atlas serve --host 127.0.0.1 --port 18790"
