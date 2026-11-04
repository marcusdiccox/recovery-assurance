FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RECOVERY_DB=/data/runs.db
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 recovery
COPY pyproject.toml README.md ./
COPY recovery_assurance ./recovery_assurance
RUN pip install --no-cache-dir --no-deps . && mkdir /data && chown recovery:recovery /data
USER recovery
EXPOSE 8765
VOLUME /data
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz')"
CMD ["uvicorn", "recovery_assurance.web:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765"]
