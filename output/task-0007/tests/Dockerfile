FROM golang:1.26-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential cmake pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG BASE_SHA=34dac209ffb6ef85cc78c5d217bbb7ad001d68fd
LABEL deepswe.source_mode="local-pinned-checkout" \
      deepswe.repository="gin-gonic/gin" \
      deepswe.base_sha="34dac209ffb6ef85cc78c5d217bbb7ad001d68fd"
COPY repo/ /app/
RUN test "$(git rev-parse HEAD)" = "$BASE_SHA" \
 && (git remote remove origin 2>/dev/null || true)
RUN go mod download
WORKDIR /tests
COPY tests/ /tests/
RUN chmod +x /tests/test.sh /tests/grader.py
CMD ["bash", "/tests/test.sh"]
