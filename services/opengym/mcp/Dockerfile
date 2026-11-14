FROM node:22-alpine

ARG OPENGYM_REV=50ab7f24e3c4ee70e7f26b3beb426f571e03414b

RUN apk add --no-cache git \
    && git init /opt/opengym \
    && cd /opt/opengym \
    && git remote add origin https://github.com/DuarteSantos8/openGym.git \
    && git sparse-checkout init --cone \
    && git sparse-checkout set mcp frontend/src catalogue \
    && git fetch --depth 1 --filter=blob:none origin "$OPENGYM_REV" \
    && git checkout --detach FETCH_HEAD \
    && npm ci --omit=dev --prefix mcp \
    && npm install --global supergateway@4.2.2 \
    && node mcp/scripts/check-node-loadable.mjs \
    && rm -rf .git \
    && apk del git \
    && npm cache clean --force

WORKDIR /opt/opengym
COPY entrypoint.sh /usr/local/bin/opengym-mcp

EXPOSE 8000
ENTRYPOINT ["sh", "/usr/local/bin/opengym-mcp"]
