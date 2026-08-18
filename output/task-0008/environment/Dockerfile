FROM python:3.12-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential cmake pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app
ARG BASE_SHA=f5988cc09713315817df6a7e327e258013a94440
LABEL deepswe.source_mode="remote-pinned-checkout" \
      deepswe.repository="TheAlgorithms/Python" \
      deepswe.base_sha="f5988cc09713315817df6a7e327e258013a94440"
RUN git init . \
 && git remote add origin https://github.com/TheAlgorithms/Python.git \
 && git fetch --depth 1 --no-tags origin "$BASE_SHA" \
 && git checkout -B main "$BASE_SHA" \
 && git remote remove origin
RUN python -m venv /opt/venv && . /opt/venv/bin/activate && if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; elif [ -f pyproject.toml ]; then pip install --no-cache-dir -e .; fi && pip install --no-cache-dir pytest==9.1.1
CMD ["bash"]
