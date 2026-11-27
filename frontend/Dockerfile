
FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Verify the environment file is available inside Docker
RUN test -f .env.local || (echo "ERROR: .env.local is missing inside Docker" && exit 1)

# Verify required variable names exist
RUN grep -q '^VITE_SUPABASE_URL=' .env|| (echo "ERROR: VITE_SUPABASE_URL missing" && exit 1)

RUN grep -q '^VITE_SUPABASE_ANON_KEY=' .env || (echo "ERROR: VITE_SUPABASE_ANON_KEY missing" && exit 1)

# Build React application
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
