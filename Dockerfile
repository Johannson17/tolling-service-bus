FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
ENV PYTHONUNBUFFERED=1
CMD ["bash","-lc","exec gunicorn -w 2 -b 0.0.0.0:${PORT:-8090} app_bus:app"]
