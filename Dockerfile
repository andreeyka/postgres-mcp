##############################################################
# Dockerfile для postgres-fastmcp (MCP-сервер на FastMCP)  #
# Multi-stage build: build → production                      #
# Настройки сборки (можно переопределить через --build-arg)  #
#                                                            #
# Большинство слоёв и объёма образа — из базового образа      #
# (python:3.12-alpine3.22).                                   #
# Наш образ добавляет ~6 слоёв и ~104 MB (.venv).            #
# Для меньшего числа слоёв: docker build --squash .          #
##############################################################

# Базовый образ Python (alpine3.21 поддерживается до Nov 2026).
# Для приватного зеркала переопредели целиком: --build-arg PYTHON_IMAGE=my.registry/python:3.12-alpine3.22
ARG PYTHON_IMAGE=python:3.12-alpine3.22
# Версия uv для установки через pip
ARG UV_VERSION=0.9.26
# Пакеты Alpine для сборки (компиляторы и библиотеки, если нужны)
ARG BUILD_PACKAGES="postgresql-libs libpq"
# Рабочая директория в контейнере
ARG UV_WORKDIR=/app


# ============================================================
# Stage 1: Сборка — установка зависимостей и компиляция
# ============================================================
FROM ${PYTHON_IMAGE} AS build
ARG BUILD_PACKAGES
ARG UV_VERSION
ARG UV_WORKDIR
ARG UV_COMPILE_BYTECODE=1

WORKDIR $UV_WORKDIR
SHELL ["/bin/sh", "-exc"]

# Устанавливаем uv через pip
RUN pip install --no-cache-dir uv==${UV_VERSION}

# Устанавливаем системные зависимости для сборки (если указаны)
RUN if [ -n "$BUILD_PACKAGES" ]; then apk add --no-cache $BUILD_PACKAGES; fi

# Настройки uv для Docker-сборки
# Меньший образ: --build-arg UV_COMPILE_BYTECODE=0 (без .pyc, медленнее старт)
ENV UV_COMPILE_BYTECODE=${UV_COMPILE_BYTECODE} \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_FROZEN=1 \
    UV_LOCKED=0

# Устанавливаем зависимости проекта (кэшируется пока uv.lock не изменится)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --no-group=dev --no-install-project

# Копируем исходный код и устанавливаем проект
COPY src/ ./src/
COPY README.md pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-group=dev --no-editable


# ============================================================
# Stage 2: Production — минимальный runtime-образ
# ============================================================
FROM ${PYTHON_IMAGE} AS production
ARG UV_WORKDIR

# Системные зависимости для runtime (libpq для PostgreSQL).
# На Alpine ctypes.find_library("pq") ищет libpq.so; пакет даёт libpq.so.5 — создаём симлинк.
RUN apk add --no-cache postgresql-libs libpq \
    && if [ ! -e /usr/lib/libpq.so ] && [ -e /usr/lib/libpq.so.5 ]; then ln -sf libpq.so.5 /usr/lib/libpq.so; fi

LABEL org.opencontainers.image.title="postgres-mcp" \
      org.opencontainers.image.description="MCP-сервер для PostgreSQL на FastMCP"

# Runtime-настройки (без UV_* — uv не нужен в production)
ENV TZ=Europe/Moscow \
    PYTHONOPTIMIZE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PATH=${UV_WORKDIR}/.venv/bin:$PATH

WORKDIR $UV_WORKDIR

# Non-root пользователь для безопасности
RUN adduser -D -h /home/app -s /bin/sh app

# Копируем только виртуальное окружение из build stage
# (--no-editable в build установил пакет внутрь .venv, src/ не нужен)
COPY --from=build --chown=app:app ${UV_WORKDIR}/.venv ${UV_WORKDIR}/.venv

USER app

# Python нативно обрабатывает SIGINT как KeyboardInterrupt
# https://hynek.me/articles/docker-signals/
STOPSIGNAL SIGINT

# Точка входа: скрипт postgres-mcp из pyproject.toml [project.scripts]
ENTRYPOINT ["postgres-mcp"]
CMD ["--transport", "http"]

EXPOSE 8000
