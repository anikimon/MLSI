# Telegram через Cloudflare (для хостов в РФ)

Telegram MTProto заблокирован для серверов в РФ. Схема обхода:

```
Telethon -> SOCKS5 127.0.0.1:1080 -> WebSocket -> Cloudflare Worker -> TCP -> Telegram DC
```

Приложение уже умеет SOCKS5 (`TELEGRAM_PROXY_HOST` / `TELEGRAM_PROXY_PORT`), поэтому код менять не нужно.

## Компоненты

- `worker.js` — Cloudflare Worker: принимает WebSocket и релеит байты в TCP (`cloudflare:sockets`).
- `relay.py` — локальный SOCKS5-сервер на сервере; открывает WebSocket к Worker.
- `tg-relay.service` — systemd-юнит для `relay.py`.

## Деплой Worker

1. Заменить `__AUTH_SECRET__` в `worker.js` на длинный случайный секрет.
2. Cloudflare Dashboard -> Workers & Pages -> Create Worker, вставить код, Deploy.
   Либо через API (`PUT /accounts/{account_id}/workers/scripts/tg-proxy`) и включить
   workers.dev-поддомен (`POST .../scripts/tg-proxy/subdomain` с `{"enabled":true}`).

## Установка на сервере

```sh
apt-get install -y python3-venv            # если нужно
python3 -m venv /srv/mlsi/app/.venv        # уже есть в проекте
/srv/mlsi/app/.venv/bin/pip install websockets

mkdir -p /srv/mlsi/tg-relay
cp relay.py /srv/mlsi/tg-relay/relay.py
cp tg-relay.service /etc/systemd/system/tg-relay.service
```

Создать `/etc/mlsi-tg-relay.env` (права `640 root:mlsi`):

```
TG_RELAY_WORKER=wss://<worker>.<subdomain>.workers.dev
TG_RELAY_TOKEN=<тот же секрет, что в worker.js>
TG_RELAY_LISTEN_HOST=127.0.0.1
TG_RELAY_LISTEN_PORT=1080
```

```sh
systemctl daemon-reload
systemctl enable --now tg-relay
```

## Настройка приложения

В `/etc/mlsi.env`:

```
TELEGRAM_PROXY_HOST=127.0.0.1
TELEGRAM_PROXY_PORT=1080
# старый MTProto-прокси отключить (задать можно только один тип):
#TELEGRAM_MTPROTO_HOST=...
#TELEGRAM_MTPROTO_PORT=...
#TELEGRAM_MTPROTO_SECRET=...
```

```sh
systemctl restart mlsi
```

## Проверка

```sh
curl -s -m 25 --socks5-hostname 127.0.0.1:1080 -o /dev/null \
  -w '%{http_code}\n' https://api.telegram.org/   # ожидаем 302
curl -s http://127.0.0.1:8000/api/telegram/status  # требуется вход в панель
```
