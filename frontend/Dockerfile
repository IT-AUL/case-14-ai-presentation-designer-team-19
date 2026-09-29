# syntax=docker/dockerfile:1.7

ARG NODE_VERSION=22
ARG NGINX_VERSION=1.28

FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
ENV CI=true

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

COPY . .
# "live" talks to the real API through /api; "mock" builds a self-contained demo (MSW).
ARG VITE_API_MODE=live
ENV VITE_API_MODE=${VITE_API_MODE}
RUN npm run build && find dist -name '*.map' -delete

FROM nginx:${NGINX_VERSION}-alpine AS runtime

ENV BACKEND_URL=http://api:8000 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    NGINX_ENVSUBST_FILTER="^(BACKEND_URL|NGINX_LOCAL_RESOLVERS)$"

RUN rm -f /etc/nginx/conf.d/default.conf
COPY --chmod=755 docker/05-backend-url.envsh /docker-entrypoint.d/05-backend-url.envsh
COPY docker/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
