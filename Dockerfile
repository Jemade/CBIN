FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
COPY src/cbin/connectors/catalogue.json /build/src/cbin/connectors/catalogue.json
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY --from=frontend /build/src/cbin/portal ./src/cbin/portal
RUN pip install --no-cache-dir . && useradd --create-home cbin
USER cbin
EXPOSE 8000
CMD ["uvicorn", "cbin.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
