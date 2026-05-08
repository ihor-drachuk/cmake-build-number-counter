FROM python:3.12-slim

WORKDIR /app/src
COPY src/ /app/src/

RUN mkdir -p /data

EXPOSE 8080

# HEALTHCHECK is honored by Docker, docker-compose, and Kubernetes.
# Railway runs its own orchestrator that ignores Dockerfile HEALTHCHECK,
# so this is inert there but useful elsewhere.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=4).status == 200 else 1)" || exit 1

ENTRYPOINT ["python", "server.py"]
CMD ["--data-dir", "/data", "--watchdog"]
