FROM rust:1.95-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential cmake pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG BASE_SHA=4ee41929eaf4fc1e5662c9b4befd05230688ca62
LABEL deepswe.source_mode="remote-pinned-checkout" \
      deepswe.repository="openai/codex" \
      deepswe.base_sha="4ee41929eaf4fc1e5662c9b4befd05230688ca62"
RUN git init . \
 && git remote add origin https://github.com/openai/codex.git \
 && git fetch --depth 1 --no-tags origin "$BASE_SHA" \
 && git checkout -B main "$BASE_SHA" \
 && git remote remove origin
RUN cargo fetch --locked --manifest-path codex-rs/Cargo.toml && cargo install just --locked
CMD ["bash"]
