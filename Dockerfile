FROM golang:1.26-bookworm AS gobuild
WORKDIR /src/components/wa_bridge
COPY components/wa_bridge/go.mod components/wa_bridge/go.sum ./
RUN go mod download
COPY components/wa_bridge/ ./
RUN CGO_ENABLED=1 go build -o /out/wa-bridge .

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata supervisor && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY hermes_bot/requirements.txt hermes_bot/requirements.txt
COPY components/wa_slash_commands/requirements.txt components/wa_slash_commands/requirements.txt
RUN pip install --no-cache-dir -r hermes_bot/requirements.txt \
                -r components/wa_slash_commands/requirements.txt
COPY . .
COPY --from=gobuild /out/wa-bridge components/wa_bridge/wa-bridge
ENV STORE_DIR=/data BRIDGE_INTERNAL_PORT=8081 PYTHONUNBUFFERED=1
RUN mkdir -p /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python3 -c "import urllib.request,os,sys; port=os.getenv('PORT','8080'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3); print('OK')"
CMD ["supervisord", "-c", "/app/supervisord.conf"]