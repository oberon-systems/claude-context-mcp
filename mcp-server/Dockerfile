FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:20-alpine AS runtime

ENV NODE_ENV=production

WORKDIR /app

# Second install so the runtime image carries no typescript or eslint tree.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=builder /app/dist ./dist

# uid of the `node` user shipped by the base image, given numerically so it
# resolves the same way on any host.
USER 1000

EXPOSE 3000

CMD ["node", "dist/index.js"]
