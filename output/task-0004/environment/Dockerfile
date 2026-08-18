FROM node:22-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG BASE_SHA=ec61f187fe39b0aa8ec6b508f2553b2047dc30cc
LABEL deepswe.source_mode="remote-pinned-checkout" \
      deepswe.repository="react/react" \
      deepswe.base_sha="ec61f187fe39b0aa8ec6b508f2553b2047dc30cc"
RUN git init . \
 && git remote add origin https://github.com/facebook/react.git \
 && git fetch --depth 1 --no-tags origin "$BASE_SHA" \
 && git checkout -B main "$BASE_SHA" \
 && git remote remove origin
RUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm install --frozen-lockfile --ignore-scripts; elif [ -f yarn.lock ]; then corepack enable && yarn install --frozen-lockfile --ignore-scripts; elif [ -f package-lock.json ]; then npm ci --ignore-scripts; fi
CMD ["bash"]
