# Build the server and the client, then ship neither toolchain.
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json tsconfig.client.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

FROM node:20-alpine AS runtime
ENV NODE_ENV=production
WORKDIR /app
# Second install so the runtime image carries no vite, react or eslint tree.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=builder /app/dist ./dist
USER 1000
EXPOSE 3002
CMD ["node", "dist/server/index.js"]
