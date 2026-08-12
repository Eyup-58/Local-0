FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

# /app/state holds the last applied batch id. Mount a volume over it (see README) — if it
# is lost, the counter resets to 0 and old signed batches become replayable again.
RUN adduser --disabled-password --gecos '' agent \
    && mkdir -p /app/state \
    && chown -R agent:agent /app

USER agent

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/entrypoint.sh"]
