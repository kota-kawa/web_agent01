# Setup notes

## Chromium remote debugging configuration

The VNC image launches Chromium through [`vnc/scripts/start-chromium.sh`](vnc/scripts/start-chromium.sh)
with remote debugging exposed on port 9222. To allow other containers (for
example the web UI) to connect, the script must keep both of the following
flags enabled and in sync with any documented allow-lists:

- `--remote-allow-origins="*"`
- `--remote-allow-ips="*"`

If you need to tighten the allow-list, update the script and these notes
together so the configuration remains accurate.
