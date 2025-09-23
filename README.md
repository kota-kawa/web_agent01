# web_agent01

This repository provides a browser-based automation environment built around a
remote Chrome instance exposed via noVNC and a web front-end for interacting
with automation tasks.

## Quick start

1. Start the services with Docker Compose:
   ```sh
   docker compose up --build
   ```
2. Open the web UI at http://localhost:5000/ once the `web` service is healthy.
3. If you need a live view of the remote browser, follow the
   [Live browser view](#live-browser-view) guidance below for access details.

## Live browser view

The `vnc` service exposes the embedded noVNC interface on TCP port `6901`. Make
sure that port is published to your host when you launch the stack so the UI can
be reached:

- **Docker Compose:** keep or add a mapping such as `ports: ["6901:6901"]` on
the `vnc` service. This is already present in `docker-compose.yml` but must
remain if you customize the configuration.
- **docker run:** include `-p 6901:6901` when running the container directly.

Once the port is available on the host, you can open the noVNC client over plain
HTTP:

```
http://<host>:6901/vnc.html?path=websockify
```

Replace `<host>` with the machine where Docker is running. For local setups this
is usually `localhost`; for remote machines, substitute the remote hostname or
IP.

### Working through SSH tunnels or alternate ports

When you tunnel the noVNC port over SSH, forward it to any local port and adjust
the URL accordingly. For example, to expose the service on local port 16901:

```
ssh -L 16901:localhost:6901 user@remote-host
http://localhost:16901/vnc.html?path=websockify
```

If you forward to the default local port 6901, the standard
`http://localhost:6901/vnc.html?path=websockify` address will work. When the
embedded web UI needs to point at an alternate host or port, update the
`NOVNC_PORT` (or `NOVNC_URL`) environment variables on the `web` service so the
iframe points to the correct endpoint.
