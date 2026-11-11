FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home app
ARG INSTALL_TOPIC_MODEL=false
COPY requirements-model.txt .
RUN if [ "$INSTALL_TOPIC_MODEL" = "true" ]; then pip install --no-cache-dir -r requirements-model.txt; fi
COPY app ./app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
