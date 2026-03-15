FROM python:3.12-slim

WORKDIR /app/src
COPY src/ /app/src/

RUN mkdir -p /data

EXPOSE 8080

ENTRYPOINT ["python", "server.py"]
CMD ["--data-dir", "/data"]
