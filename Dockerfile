FROM python:3.8-alpine

LABEL Name=Schemathesis

ENV BINPATH=/usr/local/bin
WORKDIR /app

RUN addgroup -S schemathesis && \
    adduser -D -S schemathesis -G schemathesis -s /sbin/nologin && \
    apk add --no-cache --virtual=.build-deps build-base libffi-dev openssl-dev && \
    pip install --no-cache-dir poetry==1.0.0b5 && \
    apk del .build-deps && \
    printf '#!/bin/sh\n(cd /app && poetry run schemathesis $@)' > $BINPATH/schemathesis && \
    chmod +x $BINPATH/schemathesis

USER schemathesis

COPY poetry.lock pyproject.toml ./
RUN poetry install --no-dev --no-root
COPY src ./

ENTRYPOINT ["schemathesis"]
CMD [ "--help" ]
