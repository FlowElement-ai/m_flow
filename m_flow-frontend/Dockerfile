# ── M-flow Frontend ──────────────────────────────────────────────
# Lightweight multi-stage build for the Next.js knowledge-graph UI.
# Uses pnpm for dependency management.
# ─────────────────────────────────────────────────────────────────

# Stage 1 — Install production dependencies
FROM node:25-alpine AS deps
WORKDIR /frontend
RUN corepack enable && corepack prepare pnpm@9 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Stage 2 — Build the Next.js application
FROM node:25-alpine AS build
WORKDIR /frontend
RUN corepack enable && corepack prepare pnpm@9 --activate
COPY --from=deps /frontend/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

# Stage 3 — Production runner (minimal image)
FROM node:25-alpine AS runner
WORKDIR /frontend
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup -S mflow && adduser -S mflow -G mflow

# Copy only the build artefacts needed at runtime
COPY --from=build --chown=mflow:mflow /frontend/.next/standalone ./
COPY --from=build --chown=mflow:mflow /frontend/.next/static ./.next/static
COPY --from=build --chown=mflow:mflow /frontend/public* ./public/

USER mflow
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"
CMD ["node", "server.js"]
