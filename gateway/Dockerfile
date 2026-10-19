FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY VERSION /app/VERSION
COPY gateway /app/gateway
COPY index.html Cockpit.html Login.html /app/
COPY shared /app/shared
EXPOSE 3000
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "3000"]
