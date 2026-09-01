FROM python:3.12-slim

WORKDIR /app

COPY admin/requirements.txt ./admin/requirements.txt
RUN pip install --no-cache-dir -r admin/requirements.txt

# админке нужны только модели и crud бота (без aiogram/config) —
# это не отдельная копия схемы, а тот же bot.db.models
COPY bot/__init__.py ./bot/__init__.py
COPY bot/db ./bot/db
COPY admin ./admin

CMD ["uvicorn", "admin.main:app", "--host", "0.0.0.0", "--port", "8080"]
