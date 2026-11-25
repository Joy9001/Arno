FROM python:3.14-slim

RUN groupadd --gid 10002 sandbox && useradd --uid 10002 --gid 10002 --no-create-home sandbox \
    && pip install --no-cache-dir fastapi==0.165.0 uvicorn==0.54.0 pdfplumber==0.11.11 \
        python-calamine==0.9.1 python-docx==1.2.0 pypdfium2==5.15.2 numpy==2.5.5

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY server.py .
USER 10002
EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
