FROM node:22-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential cmake pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG BASE_SHA=85293e3e2b88f4d2ce330d956b139cf628aa1e82
LABEL deepswe.source_mode="remote-pinned-checkout" \
      deepswe.repository="trekhleb/javascript-algorithms" \
      deepswe.base_sha="85293e3e2b88f4d2ce330d956b139cf628aa1e82"
RUN git init . \
 && git remote add origin https://github.com/trekhleb/javascript-algorithms.git \
 && git fetch --depth 1 --no-tags origin "$BASE_SHA" \
 && git checkout -B main "$BASE_SHA" \
 && git remote remove origin
RUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm install --frozen-lockfile --ignore-scripts; elif [ -f yarn.lock ]; then corepack enable && yarn install --frozen-lockfile --ignore-scripts; elif [ -f package-lock.json ]; then npm ci --ignore-scripts; fi
CMD ["bash"]
