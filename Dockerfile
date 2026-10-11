# Streamable HTTP server on port 8080. Build: docker build -t foncier-mcp .
# Run:   docker run -p 8080:8080 -v foncier-data:/data foncier-mcp
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/1000vario/foncier-mcp"       org.opencontainers.image.description="MCP server for French public land data"       org.opencontainers.image.licenses="MIT"

WORKDIR /app
COPY pyproject.toml LICENSE README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV FONCIER_MCP_CACHE_DIR=/data/cache \
    FONCIER_MCP_KEYS_FILE=/data/keys.json \
    FONCIER_MCP_USAGE_FILE=/data/usage.json \
    FONCIER_MCP_REQUIRE_API_KEY=1 \
    PAID_TIER_ENABLED=false
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8080

CMD ["foncier-mcp", "serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8080"]
