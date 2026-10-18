# Stdlib-only app: no browser, no pip install needed
FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PORT=10000 OUTPUT_DIR=/app/outputs
CMD ["python", "app.py"]
