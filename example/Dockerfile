FROM python:3.12-slim
WORKDIR /code
ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_RUN_PORT=5123
COPY requirements-app.txt requirements.txt
RUN pip install -r requirements.txt
EXPOSE 5123
COPY . .
CMD ["flask", "run"]
