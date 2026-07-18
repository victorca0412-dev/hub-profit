FROM python:3.12-slim
WORKDIR /app

# tzdata lets the container resolve a real timezone. Without it, glibc falls
# back to UTC no matter what TZ says, and date.today() reports the wrong day
# for evening deliveries. TZ is overridable per-deployment (see compose).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=America/New_York

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV HUBPROFIT_DB=/data/hub.db
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
