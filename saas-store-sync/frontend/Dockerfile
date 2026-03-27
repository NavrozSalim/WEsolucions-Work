FROM node:20-alpine

WORKDIR /app

ENV npm_config_update_notifier=false

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

# Dev server; host port is mapped in docker-compose (default 3001 → 3000)
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
