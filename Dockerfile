FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements.txt ./requirements-bot.txt
COPY admin/requirements.txt ./requirements-admin.txt
RUN pip install --no-cache-dir -r requirements-bot.txt -r requirements-admin.txt

COPY bot ./bot
COPY admin ./admin
COPY docker-entrypoint.py ./docker-entrypoint.py

RUN useradd --create-home --uid 1000 appuser
ENTRYPOINT ["python", "docker-entrypoint.py"]

FROM base AS bot
CMD ["python", "-m", "bot.main"]

FROM base AS web
CMD ["uvicorn", "admin.main:app", "--host", "0.0.0.0", "--port", "8000"]
