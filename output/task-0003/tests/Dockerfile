FROM python:3.12-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git ca-certificates python3 build-essential && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app
ARG BASE_SHA=8359e760be499fd8e804242e7606d81dde931abb
LABEL deepswe.source_mode="local-pinned-checkout" \
      deepswe.repository="NousResearch/hermes-agent" \
      deepswe.base_sha="8359e760be499fd8e804242e7606d81dde931abb"
COPY repo/ /app/
RUN test "$(git rev-parse HEAD)" = "$BASE_SHA" \
 && (git remote remove origin 2>/dev/null || true)
RUN python -m venv /opt/venv && . /opt/venv/bin/activate && if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; elif [ -f pyproject.toml ]; then pip install --no-cache-dir -e .; fi && pip install --no-cache-dir pytest==9.1.1
WORKDIR /tests
COPY tests/ /tests/
RUN chmod +x /tests/test.sh /tests/grader.py
CMD ["bash", "/tests/test.sh"]
