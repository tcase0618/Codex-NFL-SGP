# Serving image. Data/models are mounted at /data (built by `python -m nflquant.pipeline`).
FROM python:3.13-slim
WORKDIR /app
COPY requirements-quant.txt .
RUN pip install --no-cache-dir -r requirements-quant.txt
COPY nflquant ./nflquant
COPY terminal/quant ./terminal/quant
COPY research/stadium_points.json ./research/stadium_points.json
ENV NFLQ_DATA_DIR=/data
EXPOSE 8780
# Bind to all interfaces inside the container; publish only to localhost or behind an auth proxy.
CMD ["uvicorn", "nflquant.api:app", "--host", "0.0.0.0", "--port", "8780", "--workers", "2"]
