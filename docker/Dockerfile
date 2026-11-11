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
COPY services/validation /app/services/validation
COPY services/spacecraft_config.py /app/services/spacecraft_config.py
COPY config/spacecraft.example.json /app/config/spacecraft.example.json
COPY config/equipment-layout.example.json /app/config/equipment-layout.example.json
COPY services/sim_ground/archive.py /app/services/sim_ground/archive.py
COPY services/sim_ground/tc_journal.py /app/services/sim_ground/tc_journal.py
COPY services/sim_ground/measurement.py /app/services/sim_ground/measurement.py
COPY deploy/dgs_ops_gateway.py /app/deploy/dgs_ops_gateway.py
COPY tools/pado_packet_codec.py tools/orbit_history.py tools/preview_draft_tm.py tools/doppler_profiles.py tools/stage_fds_outbox.py /app/tools/
COPY web /app/web
# Explicitly create searchable parents: read-only file modes must never remove
# directory traversal for the non-root service user.
RUN mkdir -p /app/interfaces/command_db /app/interfaces/pado_packets
COPY interfaces/command_db/commands.json /app/interfaces/command_db/commands.json
COPY interfaces/pado_packets/draft_v0.json /app/interfaces/pado_packets/draft_v0.json
RUN chmod 0755 /app /app/config /app/interfaces /app/interfaces/command_db /app/interfaces/pado_packets \
    && chmod 0444 /app/config/spacecraft.example.json /app/config/equipment-layout.example.json /app/interfaces/command_db/commands.json /app/interfaces/pado_packets/draft_v0.json
USER 10001:10001
