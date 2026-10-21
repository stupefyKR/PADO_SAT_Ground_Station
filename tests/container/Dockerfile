FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates clang cmake gcc libclang-rt-18-dev libssh-dev libssl-dev ninja-build \
       openssh-client openssh-server pkg-config python3 sudo \
    && rm -rf /var/lib/apt/lists/*

# The test sshd accepts public keys only; the account must not be locked.
RUN useradd -m -u 10001 pado-test \
    && passwd -d pado-test \
    && mkdir -p /run/sshd

WORKDIR /src
COPY . /src
RUN cmake -S . -B build-container -G Ninja -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build-container --parallel 2
