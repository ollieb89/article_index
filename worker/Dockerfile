FROM python:3.11
WORKDIR /app
COPY requirements-shared.txt .
COPY worker/requirements.txt .
RUN pip install -r requirements.txt
COPY shared/ ./shared/
COPY worker/ .
CMD ["celery", "-A", "celery_app", "worker", "--loglevel=INFO"]
