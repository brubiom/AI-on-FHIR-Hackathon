# App image for the Structure Map Agent platform.
# Uses a STABLE Python (3.12) so the Anthropic SDK loads — the host's Python
# 3.15 alpha cannot load the SDK's `jiter` dependency. Includes psql so the
# metadata extractor can read the provider database directly.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["python", "serve.py", "--no-open", "--port", "8000"]
