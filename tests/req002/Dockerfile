# Disposable test image only; never run this against deployment data volumes.
ARG BASE_IMAGE=webui-lab/open-terminal:local
FROM ${BASE_IMAGE}
USER root
WORKDIR /opt/req002
RUN python -m pip install --no-cache-dir pytest==9.0.2
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*
COPY open_terminal ./open_terminal
COPY pyproject.toml README.md ./
# sudo strips PYTHONPATH for some real-service probes. Install this same source
# so their child interpreters cannot silently import the older base package.
RUN python -m pip install --no-cache-dir --no-deps .
COPY tests ./tests
ENV PYTHONPATH=/opt/req002
ENV PYTHONDONTWRITEBYTECODE=1
ENV REQ001_DISPOSABLE_CONTAINER=1
ENV REQ002_DISPOSABLE_CONTAINER=1
ENV USER=root
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["tests/req002", "-q", "-p", "no:cacheprovider", "--tb=short"]
