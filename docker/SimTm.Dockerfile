FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 pado-sim \
    && useradd -u 10001 -g 10001 -M -s /usr/sbin/nologin pado-sim \
    && mkdir -p /app /tm-preview /n11-inbox /fds-view \
    && chown -R 10001:10001 /tm-preview /n11-inbox

WORKDIR /app
COPY interfaces/pado_packets/draft_v0.json /app/interfaces/pado_packets/draft_v0.json
COPY tools/pado_packet_codec.py tools/preview_draft_tm.py tools/bridge_draft_gnss.py tools/run_fds_pipeline.py tools/stage_fds_outbox.py tools/publish_fds_view.py tools/orbit_history.py tools/publish_mps_schedule.py tools/doppler_profiles.py /app/tools/
COPY services/sim_sat /app/services/sim_sat
COPY services/sim_ground /app/services/sim_ground
COPY services/validation /app/services/validation
COPY services/spacecraft_config.py /app/services/spacecraft_config.py
COPY config/spacecraft.example.json /app/config/spacecraft.example.json
RUN chmod 0755 /app /app/config \
    && chmod 0444 /app/config/spacecraft.example.json
USER 10001:10001
