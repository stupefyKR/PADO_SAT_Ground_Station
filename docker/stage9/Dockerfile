FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libsodium23 python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 pado-web \
    && useradd -u 10001 -g 10001 -M -s /usr/sbin/nologin pado-web \
    && mkdir -p /app /state \
    && chown pado-web:pado-web /state

WORKDIR /app
COPY services/webapp /app/services/webapp
COPY web /app/web
COPY interfaces/command_db/commands.json /app/interfaces/command_db/commands.json
USER 10001:10001
