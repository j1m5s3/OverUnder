# Multi-stage Dockerfile for Next.js frontend
FROM node:20-slim as builder

WORKDIR /app

# Accept API URL as build arg
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build Next.js app
RUN npm run build

# Production stage
FROM node:20-slim

WORKDIR /app

# Copy built artifacts and production dependencies
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.ts ./next.config.ts

# Cloud Run sets PORT env; default to 8080
ENV PORT=8080
ENV NODE_ENV=production

# Next.js will listen on PORT
CMD ["npm", "start"]
