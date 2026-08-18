#!/bin/sh
set -eu

auth_user="${CONSOLE_AUTH_USERNAME:-admin}"
auth_password_file="${CONSOLE_AUTH_PASSWORD_FILE:-}"

if [ -n "$auth_password_file" ]; then
  if [ ! -r "$auth_password_file" ]; then
    echo "console auth password file is not readable: $auth_password_file" >&2
    exit 1
  fi
  auth_password=$(cat "$auth_password_file")
else
  auth_password="${CONSOLE_AUTH_PASSWORD:-}"
fi

if [ -z "$auth_user" ] || [ -z "$auth_password" ]; then
  echo "console auth username and password are required" >&2
  exit 1
fi

if printf '%s' "$auth_user" | grep -q '[:[:cntrl:]]'; then
  echo "console auth username contains an invalid character" >&2
  exit 1
fi

if printf '%s' "$auth_password" | grep -q '[[:cntrl:]]'; then
  echo "console auth password contains an invalid character" >&2
  exit 1
fi

auth_hash=$(printf '%s\n' "$auth_password" | mkpasswd -m sha512 -P 0)
printf '%s:%s\n' "$auth_user" "$auth_hash" > /etc/nginx/.htpasswd
chown root:nginx /etc/nginx/.htpasswd
chmod 640 /etc/nginx/.htpasswd
unset auth_password auth_hash

escape_js() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

api_base=$(escape_js "${UI_API_BASE:-/api}")
api_key=$(escape_js "${UI_BOOTSTRAP_API_KEY:-}")
admin_key=$(escape_js "${UI_BOOTSTRAP_ADMIN_KEY:-}")

cat > /usr/share/nginx/html/runtime-config.js <<EOF
window.__VIDEO_TASK_CONFIG__ = {
  apiBase: "${api_base}",
  bootstrapApiKey: "${api_key}",
  bootstrapAdminKey: "${admin_key}"
};
EOF

# Basic Auth can challenge an external runtime-config.js before the browser has
# cached credentials for subresources. Inline the same values into the guarded
# HTML so the application always receives configuration before its module runs.
api_base_b64=$(printf '%s' "${UI_API_BASE:-/api}" | base64 | tr -d '\n')
api_key_b64=$(printf '%s' "${UI_BOOTSTRAP_API_KEY:-}" | base64 | tr -d '\n')
admin_key_b64=$(printf '%s' "${UI_BOOTSTRAP_ADMIN_KEY:-}" | base64 | tr -d '\n')
runtime_inline="window.__VIDEO_TASK_CONFIG__={apiBase:atob(\"$api_base_b64\"),bootstrapApiKey:atob(\"$api_key_b64\"),bootstrapAdminKey:atob(\"$admin_key_b64\")};"
sed -i "s|/\\*__LEO_PROXY_RUNTIME_CONFIG__\\*/|$runtime_inline|" /usr/share/nginx/html/index.html
unset api_base_b64 api_key_b64 admin_key_b64 runtime_inline
