# Доверие TLS: почему `fetch_url` падал и как это устроено

## Симптом

```
fetch_url failed: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: unable to get local issuer certificate (_ssl.c:1000)>
```

При этом браузер и `node` открывают тот же адрес: они ходят со своим бандлом
корневых сертификатов. Python так не делает — он проверяет по хранилищу
операционной системы.

## Причина на конкретной машине

Windows-хранилище `ROOT` содержало **60** сертификатов, бандл `certifi` — **151**.
Цепочка `example.com` не собиралась до корня из системного набора, поэтому
рукопожатие падало ещё до HTTP. Проверяется одной командой:

```
python -c "import ssl,certifi;print(len(ssl.create_default_context().get_ca_certs()), len(ssl.create_default_context(cafile=certifi.where()).get_ca_certs()))"
```

Если первое число заметно меньше второго (или равно нулю) — дело в хранилище,
а не в сети и не в коде.

## Как теперь устроено (`core/tls.py`)

Порядок доверия, самый конкретный первым:

1. **`MESH_CA_BUNDLE`** (или `SSL_CERT_FILE`) — PEM, которому доверяет оператор;
   так подключают корпоративный прокси с инспекцией TLS. Заданный, но
   несуществующий путь игнорируется, а не роняет узел.
2. **`certifi`**, если пакет установлен — бандл Mozilla, он лежит на диске у
   любой машины, где есть `requests`/`httpx`. Именно он спасает на образах с
   устаревшим системным хранилищем.
3. **Системное хранилище** — прежнее поведение, последний вариант.

`fetch_url` перебирает хранилища по порядку и останавливается на первом,
которое реально проверило цепочку; выбранное запоминается в процессе, чтобы
следующие вызовы не тратили время на заведомо мёртвые. Если не сработало ни
одно — ошибка называет `MESH_CA_BUNDLE` и возвращает `tls_error` с текстом
от OpenSSL, а не глухое «fetch failed».

`mesh_update` использует тот же `core/tls.context()`: иначе узел с устаревшим
хранилищем не смог бы скачать собственное обновление.

## Последний вариант: `MESH_TLS_INSECURE=1`

Отключает проверку сертификата **только для `fetch_url`**. Это осознанный
компромисс для машины, где нет ни бандла, ни возможности его положить:
SSRF-защита при этом остаётся (адрес по-прежнему проверяется), но собеседник
больше не проверяется. По умолчанию выключено; включайте на свой риск и
только на время.

## Проверка

```
curl -s localhost:8096/mcp -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"fetch_url","arguments":{"url":"https://example.com"}}}'
```

Ответ должен содержать `"status": 200` и текст страницы.

---

## English (short)

Python verifies HTTPS against the OS store. On an image whose `ROOT` store is
older than the current public CAs, every fetch fails with `unable to get local
issuer certificate` while browsers and Node keep working (they ship their own
bundle). `core/tls.py` now decides trust in this order: `MESH_CA_BUNDLE` /
`SSL_CERT_FILE`, then `certifi`, then the system store; `fetch_url` tries each
store and remembers the one that verified; `mesh_update` uses the same context.
`MESH_TLS_INSECURE=1` disables certificate verification for `fetch_url` only —
an explicit last resort, off by default.
