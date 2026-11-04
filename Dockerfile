FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app:/app/generated
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python scripts/generate.py && useradd --create-home appuser
USER appuser
CMD ["uvicorn", "booking.main:app", "--host", "0.0.0.0", "--port", "8000"]
