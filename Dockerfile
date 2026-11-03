FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 1000 --create-home app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Chave só para o collectstatic; a de verdade vem do .env em tempo de execução.
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput \
    && mkdir /data && chown app:app /data \
    && chmod +x docker-entrypoint.sh
USER app
ENV DB_PATH=/data/db.sqlite3
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
# 1 processo + 4 threads cabe nos 256 MB; o SQLite serializa as escritas de qualquer forma.
CMD ["gunicorn", "config.wsgi", "-w", "1", "--threads", "4", "-b", "0.0.0.0:8000", "--max-requests", "500"]
