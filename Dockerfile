FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY scripts/ ./scripts/
COPY --from=web /web/dist ./web/dist

EXPOSE 8090
CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8090"]
