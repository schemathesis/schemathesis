FROM python:3.8-alpine

LABEL Name=Schemathesis

WORKDIR /app

COPY --chown=1000:1000 pyproject.toml README.rst src ./
RUN addgroup --gid 1000 -S schemathesis && \
    adduser --uid 1000 -D -S schemathesis -G schemathesis -s /sbin/nologin && \
    apk add --no-cache --virtual=.build-deps build-base libffi-dev openssl-dev && \
    pip install --no-cache-dir ./ && \
    apk del .build-deps

USER schemathesis
ENTRYPOINT ["schemathesis"]
