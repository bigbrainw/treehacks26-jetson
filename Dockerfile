# Processor - receives activity + EEG, runs agent
# No local monitoring. Requires ANTHROPIC_API_KEY for Claude.

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default: run processor
CMD ["python", "processor_main.py", "--port", "8765"]
