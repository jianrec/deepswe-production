FROM node:24-bookworm@sha256:934240a162082fd8b8a2f90cd5114446443f1eba1c5378f6687167ca405e6584

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates python3 build-essential \
 && rm -rf /var/lib/apt/lists/*
RUN corepack enable && corepack prepare pnpm@10.33.3 --activate

WORKDIR /app
ARG BASE_SHA=645dbf7ba364a5377ac517cd7d4aba6310b2cf78
RUN git init . \
 && git remote add origin https://github.com/freeCodeCamp/freeCodeCamp.git \
 && git fetch --depth 1 --no-tags origin "$BASE_SHA" \
 && git checkout -B main "$BASE_SHA" \
 && git remote remove origin

RUN pnpm install --frozen-lockfile --ignore-scripts \
 && pnpm --filter @freecodecamp/shared build \
 && pnpm --filter @freecodecamp/api exec prisma generate

WORKDIR /tests
COPY . /tests
RUN chmod +x /tests/test.sh /tests/grader.py
CMD ["bash", "/tests/test.sh"]
