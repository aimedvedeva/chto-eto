"""
Посредник между приложением на телефоне и GigaChat API.

Разворачивается как функция Yandex Cloud Functions (среда Python 3.12),
точка входа: index.handler.

Переменные окружения:
  GIGACHAT_AUTH_KEY  — «Ключ авторизации» из личного кабинета GigaChat API (обязательно)
  APP_SECRET         — пароль, который приложение присылает вместе с запросом (обязательно)
  GIGACHAT_MODEL     — модель, по умолчанию GigaChat-3-Ultra (подойдут и GigaChat-2-Max, GigaChat-3-Pro)
  GIGACHAT_SCOPE     — по умолчанию GIGACHAT_API_PERS (физлица)
  GIGACHAT_API_URL   — по умолчанию https://api.giga.chat/v1 (старый адрес: https://gigachat.devices.sberbank.ru/api/v1)
  GIGACHAT_OAUTH_URL — по умолчанию https://ngw.devices.sberbank.ru:9443/api/v2/oauth

Корневые сертификаты НУЦ Минцифры кладутся в папку certs/ рядом с этим файлом
(файлы *.crt или *.pem). Проверка сертификатов НЕ отключается.
"""

import base64
import glob
import hmac
import json
import os
import ssl
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

MODEL = os.environ.get("GIGACHAT_MODEL", "GigaChat-3-Ultra")
SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
API_URL = os.environ.get("GIGACHAT_API_URL", "https://api.giga.chat/v1").rstrip("/")
OAUTH_URL = os.environ.get("GIGACHAT_OAUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # у GigaChat лимит 15 МБ, нам с запасом хватит
TIMEOUT = 50  # секунд; таймаут самой функции поставить 60 с


def _ssl_context():
    ctx = ssl.create_default_context()
    for path in sorted(glob.glob(os.path.join(HERE, "certs", "*.crt")) + glob.glob(os.path.join(HERE, "certs", "*.pem"))):
        ctx.load_verify_locations(cafile=path)
    return ctx


SSL_CTX = _ssl_context()
_token = {"value": None, "expires": 0.0}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _request(url, data=None, headers=None, method="POST"):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise ApiError(502, f"GigaChat ответил {e.code}: {detail}")
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if "CERTIFICATE_VERIFY_FAILED" in reason:
            reason += " (не найден сертификат НУЦ Минцифры — положите его в папку certs/, см. README)"
        raise ApiError(502, f"Не удалось связаться с GigaChat: {reason}")


def get_token():
    now = time.time()
    if _token["value"] and now < _token["expires"] - 60:
        return _token["value"]
    auth_key = os.environ.get("GIGACHAT_AUTH_KEY", "").strip()
    if not auth_key:
        raise ApiError(500, "В функции не задан GIGACHAT_AUTH_KEY")
    data = _request(
        OAUTH_URL,
        data=f"scope={SCOPE}".encode(),
        headers={
            "Authorization": f"Basic {auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    expires_at = float(data.get("expires_at", 0))
    if expires_at > 1e12:  # в документации встречаются и миллисекунды, и секунды
        expires_at /= 1000.0
    _token["value"] = data["access_token"]
    _token["expires"] = expires_at or (now + 25 * 60)
    return _token["value"]


def upload_image(token, jpeg_bytes):
    boundary = "----chtoeto" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="purpose"\r\n\r\n'
        "general\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="photo.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + jpeg_bytes + f"\r\n--{boundary}--\r\n".encode()
    data = _request(
        f"{API_URL}/files",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    return data["id"]


def delete_file(token, file_id):
    try:
        _request(f"{API_URL}/files/{file_id}/delete", data=b"", headers={"Authorization": f"Bearer {token}"})
    except Exception:
        pass  # не страшно: файл просто останется в хранилище


def chat(token, system_prompt, user_prompt, file_id, crop_id=None):
    messages = [{"role": "system", "content": system_prompt}]
    if crop_id:
        # GigaChat принимает одно изображение на сообщение и ориентируется в основном на первое,
        # поэтому сначала идёт увеличенный фрагмент с заданием, а общий вид — вторым, для контекста
        messages.append({"role": "user", "content": user_prompt, "attachments": [crop_id]})
        messages.append({"role": "user", "content": "Фото 2 — общий вид для контекста (тот же предмет обведён красным кругом). Отвечай про предмет с фото 1.", "attachments": [file_id]})
    else:
        messages.append({"role": "user", "content": user_prompt, "attachments": [file_id]})
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "max_tokens": 1500,
    }
    data = _request(
        f"{API_URL}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------- промпты

def build_prompts(mode, has_marker, age, city, question, has_crop=False):
    if has_crop:
        where = ("Фото 1 (это сообщение) — увеличенный фрагмент вокруг места, куда ткнули пальцем. "
                 "Речь ТОЛЬКО о предмете в центре этого фрагмента. Следующим сообщением придёт общий вид для контекста "
                 "(помогает понять, где это и частью чего является предмет) — о других предметах с общего вида не рассказывай. Круг не упоминай.")
    elif has_marker:
        where = "Объект, о котором спрашивают, обведён на фото красным кругом. Если в круге несколько предметов — речь о том, что ближе к центру круга. Сам круг не упоминай."
    else:
        where = "Речь о главном объекте в центре кадра."
    place = f" Снимок сделан, скорее всего, в месте: {city}." if city else ""
    kid = f"ребёнку {age}" if age else "маленькому ребёнку"

    system = (
        "Ты — эрудированный и честный гид для мамы, которая гуляет с ребёнком и хочет рассказывать ему обо всём вокруг. "
        "Отвечай по-русски. Никогда не выдумывай имена, даты и факты: если не уверен, прямо скажи «не уверен» "
        "и объясни, что можно сказать с уверенностью. Если на фото есть табличка, подпись или этикетка — прочитай её и используй."
    )

    if mode == "history":
        task = (
            "Дай КУЛЬТУРНО-ИСТОРИЧЕСКУЮ справку об этом объекте. Если узнаёшь конкретный объект (здание, памятник, картину, скульптуру, корабль, мост и т. п.) — "
            "назови его, автора или архитектора, когда и зачем создан, в каком стиле, чем знаменит, интересные истории вокруг него. "
            "Если конкретный объект уверенно не узнаёшь — так и скажи и расскажи историю такого типа объектов."
        )
    elif mode == "ask":
        task = f"Ответь на вопрос об этом объекте: «{question}»"
    else:
        task = (
            "Объясни, ЧТО ЭТО: как называется (и как это называют специалисты, если есть особое слово), для чего нужно или как используется, "
            "из каких частей состоит или как устроено, когда и где появилось. Если это животное или растение — вид, где живёт, чем питается/как растёт."
        )

    user = (
        f"{where}{place}\n\n{task}\n\n"
        "Формат ответа (markdown, без вступлений):\n"
        "## Что это\nОдна строка: название объекта.\n"
        "## Для взрослого\n4–8 предложений или коротких пунктов с фактами.\n"
        f"## Как рассказать {kid}\n3–5 простых, живых предложений, понятных {kid}, без сложных слов.\n"
        "## Вопрос ребёнку\nОдин интересный вопрос, чтобы обсудить вместе."
    )
    return system, user


# ---------------------------------------------------------------- HTTP

def _response(status, obj):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(obj, ensure_ascii=False),
        "isBase64Encoded": False,
    }


def handler(event, context=None):
    method = (event.get("httpMethod") or "POST").upper()
    if method == "OPTIONS":
        return _response(204, {})
    if method != "POST":
        return _response(200, {"ok": True, "hint": "Это сервер приложения «Что это?». Отправьте POST-запрос."})

    try:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        req = json.loads(raw)
    except Exception:
        return _response(400, {"error": "Некорректный запрос"})

    expected = os.environ.get("APP_SECRET", "")
    # сравниваем байты: compare_digest не принимает строки с кириллицей
    if not expected or not hmac.compare_digest(str(req.get("secret", "")).encode("utf-8"), expected.encode("utf-8")):
        return _response(403, {"error": "Неверный пароль приложения (APP_SECRET)"})

    if req.get("ping"):
        return _response(200, {"ok": True, "model": MODEL})

    mode = req.get("mode", "about")
    if mode not in ("about", "history", "ask"):
        return _response(400, {"error": "Неизвестный режим"})
    question = str(req.get("question", "")).strip()[:500]
    if mode == "ask" and not question:
        return _response(400, {"error": "Пустой вопрос"})

    try:
        image = base64.b64decode(req.get("image", ""), validate=True)
    except Exception:
        return _response(400, {"error": "Не удалось прочитать фото"})
    if not image or len(image) > MAX_IMAGE_BYTES:
        return _response(400, {"error": "Фото пустое или слишком большое"})
    try:
        crop = base64.b64decode(req.get("crop") or "", validate=True)
    except Exception:
        crop = b""
    if len(crop) > MAX_IMAGE_BYTES:
        crop = b""

    system, user = build_prompts(
        mode,
        bool(req.get("marker")),
        str(req.get("age", "")).strip()[:20],
        str(req.get("city", "")).strip()[:100],
        question,
        has_crop=bool(crop),
    )

    try:
        token = get_token()
        uploaded = []
        try:
            file_id = upload_image(token, image)
            uploaded.append(file_id)
            crop_id = None
            if crop:
                crop_id = upload_image(token, crop)
                uploaded.append(crop_id)
            text = chat(token, system, user, file_id, crop_id)
        finally:
            for fid in uploaded:
                delete_file(token, fid)
    except ApiError as e:
        return _response(e.status, {"error": e.message})
    except Exception as e:  # чтобы приложение увидело понятную ошибку, а не 500 без текста
        return _response(500, {"error": f"Ошибка сервера: {type(e).__name__}: {e}"})

    return _response(200, {"text": text, "model": MODEL})
