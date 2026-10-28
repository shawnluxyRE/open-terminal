# Build an independent test image from the existing Linux runtime; never replace
# the deployment image/tag or attach its persistent volumes.
ARG BASE_IMAGE=webui-lab/open-terminal:local
FROM ${BASE_IMAGE}
USER root
WORKDIR /opt/req001
COPY pyproject.toml README.md ./
COPY open_terminal ./open_terminal
RUN python -m pip install --no-cache-dir --no-deps . \
    && python -m pip install --no-cache-dir pytest==9.0.2
COPY tests/req001 ./tests/req001
ENV PYTHONDONTWRITEBYTECODE=1
ENV REQ001_DISPOSABLE_CONTAINER=1
ENV USER=root
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["tests/req001", "-q", "-p", "no:cacheprovider", "--tb=short"]
