FROM ubuntu:24.04 AS core-builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake gcc ninja-build pkg-config libssl-dev libssh-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY CMakeLists.txt /src/CMakeLists.txt
COPY third_party /src/third_party
COPY libgs /src/libgs
COPY services /src/services
COPY sim /src/sim
RUN cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
    && cmake --build /build --target gs_stage9_core_probe --parallel 2

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libsodium23 python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 pado-web \
    && useradd -u 10001 -g 10001 -M -s /usr/sbin/nologin pado-web \
    && mkdir -p /app /state /run/core /internal \
    && chown pado-web:pado-web /state /run/core /internal

COPY --from=core-builder /build/gs_stage9_core_probe /usr/local/bin/gs_stage9_core_probe
WORKDIR /app
COPY services/webapp /app/services/webapp
COPY web /app/web
COPY interfaces/command_db/commands.json /app/interfaces/command_db/commands.json
COPY interfaces/pado_packets/draft_v0.json /app/interfaces/pado_packets/draft_v0.json
USER 10001:10001
