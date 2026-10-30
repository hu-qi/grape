# Build from repository root. Both base images must be digest-pinned at build time.
ARG NODE_IMAGE
ARG PYTHON_IMAGE
FROM ${NODE_IMAGE} AS node
FROM ${PYTHON_IMAGE}
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates openssh-client gcc g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# The CLI is a separate project. Supply its reviewed wheel locally; never put
# private repository credentials in build arguments or clone it during a build.
COPY deploy/gitlab-lab/install_cli.py /tmp/install_cli.py
COPY deploy/gitlab-lab/cli-artifacts/ /tmp/grape-cli-artifacts/
ARG GRAPE_CLI_WHEEL_SHA256
RUN python /tmp/install_cli.py --directory /tmp/grape-cli-artifacts --sha256 "${GRAPE_CLI_WHEEL_SHA256}" \
    && rm -rf /tmp/grape-cli-artifacts /tmp/install_cli.py
# Optional: explicitly selected, version-pinned npm Agent. Python/standalone
# engines need a reviewed derived image; no engine or model is silently selected.
ARG AGENT_NPM_PACKAGE=""
RUN set -eu; if [ -n "$AGENT_NPM_PACKAGE" ]; then \
      python -c 'import re,sys; sys.exit(not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+@[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?", sys.argv[1]))' "$AGENT_NPM_PACKAGE"; \
      npm install --global -- "$AGENT_NPM_PACKAGE"; \
    fi
COPY . .
COPY .env.example .env
RUN groupadd --gid 10001 grape && useradd --uid 10001 --gid grape --create-home grape \
    && mkdir -p /app/data /app/configs /app/workspaces \
    && chown -R grape:grape /app /home/grape
ARG VCS_REF
LABEL org.opencontainers.image.revision=${VCS_REF}
ENV HOME=/home/grape DEVOPS_WORKSPACE_DIR=/app/workspaces GRAPE_AUTO_DETECT_URL=false
USER 10001:10001
EXPOSE 3000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "3000", "--proxy-headers", "--forwarded-allow-ips=*"]
