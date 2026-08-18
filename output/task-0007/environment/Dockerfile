FROM golang:1.26-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential cmake pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG BASE_SHA=34dac209ffb6ef85cc78c5d217bbb7ad001d68fd
LABEL deepswe.source_mode="remote-pinned-checkout" \
      deepswe.repository="gin-gonic/gin" \
      deepswe.base_sha="34dac209ffb6ef85cc78c5d217bbb7ad001d68fd"
RUN git init . \
 && git remote add origin https://github.com/gin-gonic/gin.git \
 && git fetch --depth 1 --no-tags origin "$BASE_SHA" \
 && git checkout -B main "$BASE_SHA" \
 && git remote remove origin
RUN go mod download
CMD ["bash"]
