FROM node:22-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential cmake pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ARG BASE_SHA=beca84e600e4e250f6b244d22878e72948f331c7
LABEL deepswe.source_mode="local-pinned-checkout" \
      deepswe.repository="pmndrs/zustand" \
      deepswe.base_sha="beca84e600e4e250f6b244d22878e72948f331c7"
COPY repo/ /app/
RUN test "$(git rev-parse HEAD)" = "$BASE_SHA" \
 && (git remote remove origin 2>/dev/null || true)
RUN if [ -f pnpm-lock.yaml ]; then corepack enable && pnpm install --frozen-lockfile --ignore-scripts; elif [ -f yarn.lock ]; then corepack enable && yarn install --frozen-lockfile --ignore-scripts; elif [ -f package-lock.json ]; then npm ci --ignore-scripts; fi
WORKDIR /tests
COPY tests/ /tests/
RUN chmod +x /tests/test.sh /tests/grader.py
CMD ["bash", "/tests/test.sh"]
