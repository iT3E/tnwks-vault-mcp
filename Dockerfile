# The vault server of the tnwks agent platform: the reference file server of
# the MCP project, served over Streamable HTTP by the official MCP
# TypeScript SDK through src/serve.mjs. No secret, no config and no file of
# a share is in the image: the share is mounted at /vault at run time.
FROM docker.io/library/node:24.22.3-trixie-slim@sha256:af02d57b57719b9657a45199a79264a9437940cd55b3c7b3fc99615654f124a0

ARG VERSION=dev
ARG REVISION=unknown

LABEL org.opencontainers.image.title="tnwks-vault-mcp" \
      org.opencontainers.image.description="The reference MCP file server for one directory, over Streamable HTTP" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

WORKDIR /app
COPY package.json package-lock.json ./
# Exact versions and integrity hashes from the lock file. No package needs an
# install script.
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund && npm cache clean --force && rm -rf /root/.npm
COPY src/ ./src/

# The root file system can be read-only: the server writes only in /vault.
ENV NODE_ENV=production \
    HOME=/tmp
USER 1000:1000
EXPOSE 8080
# No entrypoint script of the base image: node starts the wrapper itself, and
# no shell runs at a start.
ENTRYPOINT []
CMD ["node", "src/serve.mjs"]
