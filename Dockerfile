# syntax=docker/dockerfile:1
FROM alpine:3.21 AS python-builder

WORKDIR /tmp

RUN apk add --no-cache \
        ca-certificates \
        wget \
        gcc \
        g++ \
        make \
        musl-dev \
        linux-headers \
        openssl-dev \
        zlib-dev \
        bzip2-dev \
        readline-dev \
        sqlite-dev \
        libffi-dev \
        xz-dev \
        ncurses-dev \
        tk-dev \
        util-linux-dev

ARG PYTHON_VERSION=3.14.3
RUN wget https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz && \
    tar -xf Python-${PYTHON_VERSION}.tar.xz

WORKDIR /tmp/Python-${PYTHON_VERSION}

# Flags match docker-library/python official images
RUN CFLAGS="-O2 -fno-omit-frame-pointer" \
    CXXFLAGS="-O2 -fno-omit-frame-pointer" \
    LDFLAGS="-Wl,--strip-all" \
    ./configure \
        --prefix=/opt/python \
        --enable-shared \
        --with-lto \
        --enable-optimizations \
        --disable-gil \
        --disable-test-modules \
        --with-system-ffi \
        --with-ensurepip=install

RUN make -j$(nproc) && make install

RUN find /opt/python -depth \
    \( \
        \( -type d -a \( -name test -o -name tests -o -name idle_test \) \) \
        -o \( -type f -a \( -name '*.pyc' -o -name '*.pyo' -o -name 'libpython*.a' \) \) \
    \) -exec rm -rf '{}' + && \
    rm -rf /opt/python/lib/python3.14/test \
           /opt/python/lib/python3.14/tkinter \
           /opt/python/lib/python3.14/turtledemo \
           /opt/python/lib/python3.14/idlelib

FROM alpine:3.21 AS app-builder

WORKDIR /app

COPY --from=python-builder /opt/python /opt/python

ENV PATH="/opt/python/bin:$PATH"
ENV LD_LIBRARY_PATH="/opt/python/lib"

RUN apk add --no-cache \
        gcc \
        g++ \
        musl-dev \
        libffi-dev \
        openssl-dev \
        cargo \
        rust

RUN python3.14t -m venv /opt/venv

COPY pyproject.toml README.md ./

RUN mkdir -p src/schemathesis && \
    touch src/schemathesis/__init__.py

RUN /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir ./

COPY src ./src

RUN /opt/venv/bin/pip install --no-cache-dir --no-deps --force-reinstall ./

RUN find /opt/venv -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete && \
    find /opt/venv -type d -name __pycache__ -delete

FROM alpine:3.21 AS runtime

LABEL Name=Schemathesis

WORKDIR /app

COPY --from=python-builder /opt/python /opt/python

COPY --from=app-builder /opt/venv /opt/venv

RUN apk add --no-cache ca-certificates libgcc libstdc++

RUN addgroup --gid 1000 -S schemathesis && \
    adduser --uid 1000 -D -S schemathesis -G schemathesis -s /sbin/nologin

COPY --chown=1000:1000 pyproject.toml README.md src ./

RUN chown -R 1000:1000 /app

USER schemathesis

ENV PATH="/opt/venv/bin:/opt/python/bin:$PATH"
ENV LD_LIBRARY_PATH="/opt/python/lib"
ENV VIRTUAL_ENV=/opt/venv

ENV PYTHON_GIL=0
ENV SCHEMATHESIS_DOCKER_IMAGE=3.14t-alpine

ENTRYPOINT ["schemathesis"]
