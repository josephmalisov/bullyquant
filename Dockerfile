FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite DB + HTML reports live here; mount a Railway volume at this path so
# state survives between cron runs.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

CMD ["python", "-m", "lab", "daily"]
