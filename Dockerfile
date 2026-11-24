FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./


RUN python -m pip install --no-cache-dir -r requirements.txt

FROM base AS runtime


CMD ["python", "--version"]


FROM base AS development

COPY requirements-dev.txt ./

RUN python -m pip install --no-cache-dir -r requirements-dev.txt

COPY . .

RUN python -m pip install --no-cache-dir --no-deps -e .

CMD ["python", "-m", "ruff", "check", "."]
