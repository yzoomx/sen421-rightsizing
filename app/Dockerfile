FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gen_data.py main.py ./
RUN python gen_data.py /app/cards.db
ENV WORKERS=2
EXPOSE 8000
# One worker process per vCPU (WORKERS is set to $(nproc) at docker run).
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS} --no-access-log --log-level warning"]
