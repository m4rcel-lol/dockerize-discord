FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev openssl-dev \
    && addgroup -S dockerize \
    && adduser -S dockerize -G dockerize

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY bot ./bot
COPY data ./data

RUN mkdir -p /app/data \
    && chown -R dockerize:dockerize /app

USER dockerize

CMD ["python", "-m", "bot.main"]
