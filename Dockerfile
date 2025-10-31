# Use official Playwright image to get all browser deps preinstalled
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Set workdir
WORKDIR /app

# Copy project files
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest
COPY . .

# Ensure browsers are installed (usually already in base image, but safe to run)
RUN playwright install chromium

# Expose port (Render will inject PORT env var)
ENV PORT=10000

# Default command runs the API (on-demand trigger + static file server)
CMD ["bash", "-lc", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
