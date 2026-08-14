# The grounds: a browser with a health check bolted to the side.
#
# Built from the repository root, so that the build context matches the other
# three images and .dockerignore is the one file deciding what reaches any of
# them:
#   docker build -f grounds/Dockerfile -t grounds .

FROM python:3.14-slim
WORKDIR /app

# Pinned for the same reason as the arena's: `uv sync --frozen` fixes every
# dependency, and a floating uv would leave the tool doing the pinning as the
# one part of the image free to drift.
COPY --from=ghcr.io/astral-sh/uv:0.11.23 /uv /usr/local/bin/uv

COPY grounds/pyproject.toml grounds/uv.lock ./
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# The browser is downloaded by the Playwright that was just installed, rather
# than taken from a base image that names a version of its own: a client and a
# browser build are one version and the pin is in pyproject.toml. --with-deps
# is the apt half - Chromium on a slim base is a binary with no shared
# libraries to load, and the failure is at launch rather than here.
#
# The path is set above rather than left to default, because the default is
# under $HOME and the whole point of an image is that it does not depend on who
# is running it.
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY grounds/ ./

# Cloud Run names the port. 8004 is the default for running the image by hand,
# and the same number main.py falls back to.
ENV PORT=8004
EXPOSE 8004

# No uvicorn on the command line: this server starts a browser before it starts
# listening, so the process is the application's own and reads PORT itself.
CMD ["python", "main.py"]
