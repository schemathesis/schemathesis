FROM python:3.14-alpine

LABEL Name=Schemathesis

WORKDIR /app

RUN addgroup --gid 1000 -S schemathesis && \
    adduser --uid 1000 -D -S schemathesis -G schemathesis -s /sbin/nologin

COPY --chown=1000:1000 pyproject.toml README.md src ./

RUN apk add --no-cache --virtual=.build-deps build-base libffi-dev curl openssl-dev && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    source $HOME/.cargo/env && \
    pip install --upgrade pip && pip install --no-cache-dir ./ && \
    apk del .build-deps && \
    rustup self uninstall -y

# Needed for the `.hypothesis` directory
RUN chown -R 1000:1000 /app

USER schemathesis

ENV SCHEMATHESIS_DOCKER_IMAGE=3.14-alpine

ENTRYPOINT ["schemathesis"]
