#!/bin/bash
# One-time setup for the Research Machine's own relay server (Ubuntu, AWS EC2 "User data" or run as root).
# BEFORE PASTING: replace CHANGE_ME below with your own long random password (letters and digits, no spaces).
# Put the SAME password in GitHub: autopilot -> Settings -> Secrets and variables -> Actions -> New secret "RM_RELAY_KEY".
RELAY_KEY="CHANGE_ME"

set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y curl unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades          # automatic security updates
apt-get install -y nodejs                                         # Ubuntu's own Node.js (24.04+ ships 18 or newer)
if ! node -e 'process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)'; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs
fi

mkdir -p /opt/relay && cd /opt/relay
for f in server.js live.js package.json; do
  curl -fsSL "https://raw.githubusercontent.com/dukaelementsdotcom/autopilot/main/relay-render/$f" -o "$f"
done

cat > /etc/systemd/system/relay.service <<EOF
[Unit]
Description=Research Machine live relay
After=network-online.target
[Service]
WorkingDirectory=/opt/relay
Environment=PORT=8080
Environment=RELAY_KEY=${RELAY_KEY}
Environment=RELAY_REGION=aws-frankfurt
Environment=RELAY_WORKERS=4
ExecStart=/usr/bin/node /opt/relay/server.js
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

# keep the relay code up to date with the repo once a day (restart only if something changed)
cat > /etc/cron.daily/relay-update <<'EOF'
#!/bin/bash
cd /opt/relay || exit 0
changed=0
for f in server.js live.js package.json; do
  curl -fsSL "https://raw.githubusercontent.com/dukaelementsdotcom/autopilot/main/relay-render/$f" -o "/tmp/$f" || exit 0
  cmp -s "/tmp/$f" "$f" || { cp "/tmp/$f" "$f"; changed=1; }
done
[ $changed = 1 ] && systemctl restart relay
EOF
chmod +x /etc/cron.daily/relay-update

systemctl daemon-reload
systemctl enable --now relay
