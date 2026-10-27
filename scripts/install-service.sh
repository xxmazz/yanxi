#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
project_dir="$PWD"
mkdir -p runtime
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/yanxi.service" <<EOF
[Unit]
Description=Yanxi local research assistant
After=network.target

[Service]
Type=simple
WorkingDirectory=$project_dir
ExecStart=/usr/bin/bash "$project_dir/scripts/serve.sh"
StandardOutput=append:$project_dir/runtime/server.log
StandardError=inherit
Restart=on-failure
RestartSec=10
TimeoutStartSec=300
TimeoutStopSec=60

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now yanxi
