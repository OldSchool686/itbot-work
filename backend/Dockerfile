FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app

# psycopg[binary] ships prebuilt wheels — no compilation needed
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

COPY . /app/backend

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
