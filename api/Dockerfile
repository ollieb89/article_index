FROM python:3.11
WORKDIR /app
COPY requirements-shared.txt .
COPY api/requirements.txt .
RUN pip install -r requirements.txt
COPY shared/ ./shared/
COPY api/ .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
