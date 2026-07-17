FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite DB + HTML reports live here. Attach a Railway Volume mounted at this
# path (Settings -> Volumes in the Railway dashboard) so state survives
# between cron runs; Railway's builder doesn't support the Dockerfile VOLUME
# instruction, so this is a plain directory, not a declared volume.
RUN mkdir -p /app/data

CMD ["python", "-m", "lab", "daily"]
