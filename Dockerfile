FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh && mkdir -p /app/data

# Persistenter Appdata-Pfad, in Unraid auf /mnt/user/appdata/mein_botname gemappt.
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/database.db

# Flask-Dashboard. Die interne Bot-API (Port 8080) bleibt containerintern
# und wird nicht nach außen exposed.
EXPOSE 5000

CMD ["./start.sh"]
