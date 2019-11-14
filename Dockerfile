FROM python:3.8-alpine

LABEL Name=Schemathesis

RUN python3 -m pip install --upgrade pip && \
    python3 -m pip --no-cache-dir install poetry

WORKDIR /app
RUN addgroup -S schemathesis && \
    adduser -D -S schemathesis -G schemathesis -s /sbin/nologin && \
    chown schemathesis:schemathesis /app -R
USER schemathesis
COPY --chown=schemathesis:schemathesis ./poetry.lock ./pyproject.toml ./README.rst ./src ./
RUN poetry install --no-dev

ENTRYPOINT ["poetry", "run", "schemathesis"]
CMD [ "--help" ]
