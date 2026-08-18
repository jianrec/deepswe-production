FROM rust:1.95-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential cmake pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG BASE_SHA=ee20f426ddf338ac7ead5c5f00ea49258005caaf
LABEL deepswe.source_mode="remote-pinned-checkout" \
      deepswe.repository="sharkdp/fd" \
      deepswe.base_sha="ee20f426ddf338ac7ead5c5f00ea49258005caaf"
RUN git init . \
 && git remote add origin https://github.com/sharkdp/fd.git \
 && git fetch --depth 1 --no-tags origin "$BASE_SHA" \
 && git checkout -B main "$BASE_SHA" \
 && git remote remove origin
RUN cargo fetch --locked
CMD ["bash"]
