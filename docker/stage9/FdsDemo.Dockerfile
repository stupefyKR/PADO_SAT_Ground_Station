FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git openjdk-17-jdk-headless maven python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 pado-fds \
    && useradd -u 10001 -g 10001 -M -s /usr/sbin/nologin pado-fds

WORKDIR /app
COPY services/fds /app/services/fds
COPY tools/build_iss_demo.py tools/build_synthetic_demo.py tools/publish_fds_view.py tools/publish_mps_schedule.py tools/stage_fds_outbox.py tools/run_fds_pipeline.py tools/receive_fds_inputs.py /app/tools/
RUN git clone --depth 1 https://gitlab.orekit.org/orekit/orekit-data.git /opt/orekit-data \
    && git -C /opt/orekit-data fetch --depth 1 origin 2422a7d06ee2cc3b5a503a47271203b0706f3e82 \
    && git -C /opt/orekit-data checkout --detach 2422a7d06ee2cc3b5a503a47271203b0706f3e82 \
    && test "$(git -C /opt/orekit-data rev-parse HEAD)" = 2422a7d06ee2cc3b5a503a47271203b0706f3e82 \
    && mvn -B -ntp -Dmaven.repo.local=/app/m2 -f /app/services/fds/pom.xml test-compile exec:help \
    && printf '#!/bin/sh\nexec mvn -o "$@"\n' > /usr/local/bin/mvn-offline \
    && chmod 0755 /usr/local/bin/mvn-offline \
    && mkdir -p /app/out/fds-view /app/out/fds-truth /app/out/mps-schedules \
    && chown -R 10001:10001 /app /opt/orekit-data

USER 10001:10001
CMD ["python3", "tools/build_synthetic_demo.py", "--test-only", "--orekit-data", "/opt/orekit-data", "--mvn", "/usr/local/bin/mvn-offline", "--maven-repo", "/app/m2", "--output-root", "/app/out/scenario-ltan12-v1", "--n11-inbox", "/n11-inbox/scenario-ltan12-v1", "--watch", "--refresh-seconds", "900"]
