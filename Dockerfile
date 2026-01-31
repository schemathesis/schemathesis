FROM alpine:3.21

LABEL Name=Schemathesis

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Configure uv to use managed Python in a shared location
ENV UV_PYTHON_PREFERENCE=only-managed
ENV UV_PYTHON_INSTALL_DIR=/opt/python

# Install free-threaded Python 3.14 to shared location
RUN uv python install 3.14t

RUN addgroup --gid 1000 -S schemathesis && \
    adduser --uid 1000 -D -S schemathesis -G schemathesis -s /sbin/nologin

COPY --chown=1000:1000 pyproject.toml README.md src ./

# Create virtual environment with free-threaded Python
RUN uv venv --python 3.14t /opt/venv

# Install runtime dependencies
RUN apk add --no-cache libgcc

RUN apk add --no-cache --virtual=.build-deps build-base libffi-dev curl openssl-dev && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    source $HOME/.cargo/env && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache-dir ./ && \
    apk del .build-deps && \
    rustup self uninstall -y

# Needed for the `.hypothesis` directory
RUN chown -R 1000:1000 /app

USER schemathesis

# Set PATH to use the virtual environment
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV=/opt/venv

# Disable GIL at runtime for true parallelism with --workers
ENV PYTHON_GIL=0
ENV SCHEMATHESIS_DOCKER_IMAGE=3.14t-alpine

ENTRYPOINT ["schemathesis"]
