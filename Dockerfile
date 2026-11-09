FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home bot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The mounted ./data folder must be writable by uid 1000.
USER bot
ENV MPLCONFIGDIR=/tmp/matplotlib

CMD ["python", "main.py"]
