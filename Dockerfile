FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

# Copy application
COPY . .

# Environment variables
ENV PYTHONUNBUFFERED=1

# Run the watcher
CMD ["python", "-m", "app.main"]
