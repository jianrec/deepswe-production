FROM rust:1.95-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential cmake pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG BASE_SHA=ee20f426ddf338ac7ead5c5f00ea49258005caaf
LABEL deepswe.source_mode="local-pinned-checkout" \
      deepswe.repository="sharkdp/fd" \
      deepswe.base_sha="ee20f426ddf338ac7ead5c5f00ea49258005caaf"
COPY repo/ /app/
RUN test "$(git rev-parse HEAD)" = "$BASE_SHA" \
 && (git remote remove origin 2>/dev/null || true)
RUN cargo fetch --locked --manifest-path codex-rs/Cargo.toml && cargo install just --locked
WORKDIR /tests
COPY tests/ /tests/
RUN chmod +x /tests/test.sh /tests/grader.py
CMD ["bash", "/tests/test.sh"]
