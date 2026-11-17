ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-aiohttp

COPY app /app

WORKDIR /app
# Run Python directly instead of through the base image's s6-overlay /init,
# which would reset the environment and drop SUPERVISOR_TOKEN.
ENTRYPOINT []
CMD ["python3", "-u", "/app"]
