FROM python:3.13-slim

WORKDIR /app

RUN pip install fastapi uvicorn

COPY app.py .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
