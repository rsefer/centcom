docker compose stop
docker compose pull --ignore-buildable
docker compose build caddy
docker compose up -d --remove-orphans
