FROM golang:1.26-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG BASE_SHA=144893850fa778c8c81ff931f26614d62e6689c1
LABEL deepswe.source_mode="remote-pinned-checkout" \
      deepswe.repository="ollama/ollama" \
      deepswe.base_sha="144893850fa778c8c81ff931f26614d62e6689c1"
RUN git init . \
 && git remote add origin https://github.com/ollama/ollama.git \
 && git fetch --depth 1 --no-tags origin "$BASE_SHA" \
 && git checkout -B main "$BASE_SHA" \
 && git remote remove origin
RUN go mod download
CMD ["bash"]
