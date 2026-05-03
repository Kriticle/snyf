FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY functions/ ./functions/
COPY services/ ./services/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN useradd -m appuser
USER appuser

CMD ["uvicorn", "services.search_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

