#!/bin/sh
set -eu

: "${SUPERGATEWAY_API_KEY:?Set OPENGYM_MCP_ACCESS_TOKEN to a random client access token}"
: "${OPENGYM_TOKEN:?Set OPENGYM_MCP_PAIRING_TOKEN using the openGym pairing script}"

exec supergateway \
	--stdio "node /opt/opengym/mcp/src/index.js" \
	--outputTransport streamableHttp \
	--stateful \
	--sessionTimeout 300000 \
	--port 8000 \
	--healthEndpoint /healthz \
	--healthCheck server
