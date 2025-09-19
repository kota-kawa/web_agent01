# Web Agent Docker Configuration Options

This directory contains multiple Docker Compose configurations to handle different deployment scenarios:

## Quick Start (Web Service Only)

If you want to quickly access the web interface at http://127.0.0.1:5000 without the full automation service:

```bash
docker compose -f docker-compose-web-only.yml up --build
```

This will start only the web service and make it accessible at http://127.0.0.1:5000.

## Full Service (Web + VNC)

For the complete automation service with browser automation capabilities:

```bash
docker compose up --build
```

## SSL Certificate Issues

If you encounter SSL certificate verification errors during the Docker build process, this is due to certificate validation in the build environment. The fixes applied include:

1. **Python pip**: Added `--trusted-host` flags for PyPI domains
2. **Node.js/Playwright**: Added `NODE_TLS_REJECT_UNAUTHORIZED=0` for browser downloads

## Environment Setup

Make sure you have a `.env` file in the project root. A minimal example:

```
GEMINI_API_KEY=
GROQ_API_KEY=
```

## Troubleshooting

- **Port 5000 already in use**: Stop any existing Flask applications or change the port mapping
- **VNC service fails to build**: Use the web-only configuration as an alternative
- **Certificate errors**: The provided fixes should resolve most SSL issues in build environments