# Семантический retrieval для цифровых фокус-групп

Локальные эмбеддинги без внешних API. Схема:

```
digital_focus_sources (комментарии Telegram)
        │  эмбеддинги (один раз, кэш в source_vectors)
        ▼
mlsi-embeddings.service  (fastembed, 127.0.0.1:8091)
        ▲  embed(query)
        │
server.py: semantic_evidence()  = 0.5 * cosine + 0.5 * lexical
```

## Почему именно так

- Сервер дешёвый (1 ГБ RAM), поэтому нужен **swap** (2 ГБ) — добавлен в `/etc/fstab`.
- Модель `minishlab/potion-multilingual-128M` (статическая, дистиллят e5): лучше держит русский
  retrieval, чем `paraphrase-multilingual-MiniLM`, и очень быстрая (≈600 текстов за 3.5 c).
- Веса моделей тянутся через зеркало `HF_ENDPOINT=https://hf-mirror.com` (CDN HuggingFace в РФ заблокирован).
- Векторы кэшируются в таблице `source_vectors` (float32 blob), считаются в фоне при создании группы.

## Файлы

- `emb_server.py` — HTTP-сервис эмбеддингов (stdlib http.server + fastembed).
- `mlsi-embeddings.service` — systemd-юнит.

## Установка

```sh
# swap (один раз)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

/srv/mlsi/app/.venv/bin/pip install fastembed

mkdir -p /srv/mlsi/embeddings
cp emb_server.py /srv/mlsi/embeddings/emb_server.py
cp mlsi-embeddings.service /etc/systemd/system/mlsi-embeddings.service
systemctl daemon-reload && systemctl enable --now mlsi-embeddings
```

Проверка: `curl -s http://127.0.0.1:8091/health`.

## Отключение

В `/etc/mlsi.env` задать `EMBEDDINGS_URL=` (пусто) и перезапустить `mlsi` —
вернётся лексический retrieval.
