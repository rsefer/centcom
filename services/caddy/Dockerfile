FROM caddy:2-builder-alpine AS builder

RUN xcaddy build \
    --with github.com/caddy-dns/route53

FROM node:22-alpine AS dashboard

WORKDIR /app
COPY www/package.json www/package-lock.json ./
RUN npm ci
COPY www/ ./
RUN npm run build

FROM caddy:2-alpine

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY --from=dashboard /app/dist /usr/share/caddy
