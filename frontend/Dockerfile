# Build the static bundle, serve it with Caddy, proxy /api and /ws to the api
# service. This container is the LOCAL full-stack topology (docker compose up)
# and a rehearsal of production: in prod the same Caddy role runs on the EC2
# box and the static half lives on Vercel instead.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
