FROM python:3.12-slim-bookworm

RUN pip install --no-cache-dir websockets==15.0.1

WORKDIR /app
COPY app.py /app/app.py
COPY run.sh /app/run.sh
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
