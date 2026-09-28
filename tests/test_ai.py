"""Yapay zekâ katmanı (Claude / Mistral / OpenAI uyumlu), fiş okuma, doğal dil ve /sor.

Gerçek API çağrılmaz: pano.ai._post taklit edilir, giden istek incelenir.
Çalıştır: .venv/Scripts/python tests/test_ai.py
"""
import base64
import io
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.ai as ai  # noqa: E402
import pano.telegram as tg  # noqa: E402
from pano.db import execute, query_one  # noqa: E402
from pano.utils import today  # noqa: E402

REQUESTS = []   # (url, headers, body)
REPLIES = []    # sırayla dönülecek cevaplar (dict ya da Exception)


def fake_post(url, headers, body):
    REQUESTS.append((url, headers, body))
    reply = REPLIES.pop(0)
    if isinstance(reply, Exception):
        raise reply
    return reply


ai._post = fake_post


def claude_reply(payload, stop="end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "stop_reason": stop}


def openai_reply(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


def set_env(**values):
    for k in ("AI_PROVIDER", "AI_API_KEY", "AI_MODEL", "AI_BASE_URL", "AI_EFFORT"):
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in values.items() if v is not None})


def png():
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), (255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_config():
    set_env()
    assert ai.config() is None and "AI_PROVIDER" in ai.missing_reason()
    set_env(AI_PROVIDER="mistral")
    assert ai.config() is None and "AI_API_KEY" in ai.missing_reason()
    set_env(AI_PROVIDER="mistral", AI_API_KEY="k")
    cfg = ai.config()
    assert (cfg["model"], cfg["base_url"], cfg["style"]) == ("mistral-medium-latest", "https://api.mistral.ai/v1", "openai")
    set_env(AI_PROVIDER="anthropic", AI_API_KEY="k")
    assert ai.config()["model"] == "claude-opus-5"
    set_env(AI_PROVIDER="openai", AI_API_KEY="k")
    assert ai.config() is None and "AI_MODEL" in ai.missing_reason()
    set_env(AI_PROVIDER="openai-compatible", AI_API_KEY="k", AI_MODEL="llama3")
    assert ai.config() is None and "AI_BASE_URL" in ai.missing_reason()
    set_env(AI_PROVIDER="openai-compatible", AI_API_KEY="k", AI_MODEL="llama3", AI_BASE_URL="http://localhost:11434/v1/")
    assert ai.config()["base_url"] == "http://localhost:11434/v1"
    set_env(AI_PROVIDER="bilinmeyen", AI_API_KEY="k")
    assert "Bilinmeyen" in ai.missing_reason()
    print("  config OK")


SCHEMA = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"], "additionalProperties": False}


def test_claude_request():
    set_env(AI_PROVIDER="anthropic", AI_API_KEY="sk-test")
    REPLIES.append(claude_reply({"x": 1}))
    assert ai.complete_json("sistem", "metin", SCHEMA, image=b"\xff\xd8jpeg") == {"x": 1}
    url, headers, body = REQUESTS[-1]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-test" and headers["anthropic-version"] == "2023-06-01"
    assert headers["anthropic-beta"] == "server-side-fallback-2026-07-01" and body["fallbacks"] == "default"
    assert body["model"] == "claude-opus-5" and body["system"] == "sistem" and body["max_tokens"] == 16000
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "medium"}
    image, text = body["messages"][0]["content"]
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.standard_b64encode(b"\xff\xd8jpeg").decode()}}
    assert text == {"type": "text", "text": "metin"}
    # Haiku 4.5: effort ve fallbacks gönderilmez
    set_env(AI_PROVIDER="anthropic", AI_API_KEY="k", AI_MODEL="claude-haiku-4-5")
    REPLIES.append(claude_reply({"x": 2}))
    ai.complete_json("s", "t", SCHEMA)
    _url, headers, body = REQUESTS[-1]
    assert "fallbacks" not in body and "anthropic-beta" not in headers and "effort" not in body["output_config"]
    # Ret ve yarım kalma hataya dönüşür
    REPLIES.append(claude_reply("", stop="refusal"))
    try:
        ai.complete_json("s", "t", SCHEMA)
        raise AssertionError("refusal hata vermeli")
    except ai.AIError as e:
        assert "reddetti" in str(e)
    print("  Claude request OK")


def test_gemini_preset():
    set_env(AI_PROVIDER="gemini", AI_API_KEY="AIza-test")
    cfg = ai.config()
    assert (cfg["model"], cfg["style"]) == ("gemini-flash-latest", "openai")
    REPLIES.append({"choices": [{"message": {"content": "Merhaba"}, "finish_reason": "stop"}]})
    with app.app_context():
        assert ai.complete_text("sistem", "selam") == "Merhaba"
    url, headers, body = REQUESTS[-1]
    assert url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert headers["Authorization"] == "Bearer AIza-test" and body["model"] == "gemini-flash-latest"
    set_env(AI_PROVIDER="gemini", AI_API_KEY="AIza-test", AI_MODEL="gemini-flash-lite-latest")
    assert ai.config()["model"] == "gemini-flash-lite-latest"
    print("  gemini preset OK")


def test_mistral_request():
    set_env(AI_PROVIDER="mistral", AI_API_KEY="ms-key")
    REPLIES.append(openai_reply({"x": 3}))
    assert ai.complete_json("sistem", "metin", SCHEMA, image=b"img") == {"x": 3}
    url, headers, body = REQUESTS[-1]
    assert url == "https://api.mistral.ai/v1/chat/completions" and headers["Authorization"] == "Bearer ms-key"
    assert body["model"] == "mistral-medium-latest"
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "cevap", "schema": SCHEMA, "strict": True}}
    assert body["messages"][0] == {"role": "system", "content": "sistem"}
    parts = body["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "metin"}
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.standard_b64encode(b"img").decode()}}
    # Parça listesi olarak dönen içerik
    REPLIES.append({"choices": [{"message": {"content": [{"type": "text", "text": '{"x": 4}'}]}, "finish_reason": "stop"}]})
    assert ai.complete_json("s", "t", SCHEMA) == {"x": 4}
    # Şema desteklenmiyorsa JSON moduna bir kez düşer
    err = ai.AIError("HTTP 400: response_format")
    err.status = 400
    REPLIES.extend([err, openai_reply('```json\n{"x": 5}\n```')])
    assert ai.complete_json("s", "t", SCHEMA) == {"x": 5}
    assert REQUESTS[-1][2]["response_format"] == {"type": "json_object"} and "JSON şemasına" in REQUESTS[-1][2]["messages"][0]["content"]
    # Metin cevabı
    REPLIES.append(openai_reply("Merhaba!"))
    assert ai.complete_text("s", "t") == "Merhaba!" and "response_format" not in REQUESTS[-1][2]
    print("  Mistral / OpenAI-compatible request OK")


# ---------- Uygulama akışları (Mistral ile) ----------
with app.app_context():
    execute("UPDATE users SET telegram_chat_id = '100', ai_enabled = 1 WHERE username = 'admin'")
with app.test_request_context():
    SECRET = tg.webhook_secret()
CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 9}
tg.download_file = lambda file_id: png()
RAW = app.test_client()
UPDATE = [0]


def send(**msg):
    UPDATE[0] += 1
    update = {"update_id": UPDATE[0], "message": {"message_id": UPDATE[0], "chat": {"id": 100}, **msg}}
    RAW.post("/telegram/webhook", data=json.dumps(update), content_type="application/json",
             headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    return update


def press(data):
    UPDATE[0] += 1
    cq = {"id": "c", "data": data, "from": {"id": 100}, "message": {"message_id": 9, "chat": {"id": 100}}}
    RAW.post("/telegram/webhook", data=json.dumps({"update_id": UPDATE[0], "callback_query": cq}),
             content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def last(method):
    return [p for m, p in CALLS if m == method][-1]


def datas(params):
    return [b["callback_data"] for row in json.loads(params.get("reply_markup", '{"inline_keyboard": []}'))["inline_keyboard"] for b in row]


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


RECEIPT = {"is_receipt": True, "kind": "expense", "merchant": "Migros", "total": 412.3, "currency": "TRY",
           "date": today().isoformat(), "due_date": None, "category": "Market", "summary": "12 ürün"}


def test_telegram_receipt():
    set_env(AI_PROVIDER="mistral", AI_API_KEY="k")
    send(photo=[{"file_id": "f1"}])
    assert "ph:ai" in datas(last("sendMessage"))
    REPLIES.append(openai_reply(RECEIPT))
    press("ph:ai")
    card = last("editMessageText")
    assert "Fiş okundu" in card["text"] and "412,30 ₺" in card["text"] and "Migros" in card["text"]
    assert datas(card) == ["rc:exp", "rc:x"]
    sent_image = REQUESTS[-1][2]["messages"][1]["content"][1]["image_url"]["url"]
    assert sent_image.startswith("data:image/jpeg;base64,")  # PNG JPEG'e çevrildi
    press("rc:exp")
    e = one("SELECT * FROM expenses ORDER BY id DESC LIMIT 1")
    assert (e["amount"], e["category"], e["note"]) == (412.3, "Market", "Migros")
    assert "Harcama kaydedildi" in last("editMessageText")["text"]
    # Fatura
    bill = {**RECEIPT, "kind": "bill", "merchant": "Enerjisa", "total": 845.5, "category": "Faturalar",
            "due_date": (today() + timedelta(days=10)).isoformat()}
    send(photo=[{"file_id": "f2"}])
    REPLIES.append(openai_reply(bill))
    press("ph:ai")
    assert datas(last("editMessageText"))[0] == "rc:bill"
    press("rc:bill")
    b = one("SELECT * FROM bills ORDER BY id DESC LIMIT 1")
    assert (b["name"], b["amount"], b["due_date"], b["remind"]) == ("Enerjisa", 845.5, bill["due_date"], 1)
    # Fiş değilse
    send(photo=[{"file_id": "f3"}])
    REPLIES.append(openai_reply({**RECEIPT, "is_receipt": False, "total": None}))
    press("ph:ai")
    assert "fiş ya da fatura gibi görünmüyor" in last("editMessageText")["text"]
    # Normalleştirme: bilinmeyen kategori -> Diğer, gelecek tarih -> bugün
    from pano import assistant
    REPLIES.append(openai_reply({**RECEIPT, "category": "Uzay", "date": "2099-01-01", "total": "99.9"}))
    with app.app_context():
        r = assistant.read_receipt(png())
    assert (r["category"], r["date"], r["total"]) == ("Diğer", today().isoformat(), 99.9)
    print("  telegram receipt OK")


def intent(**kw):
    base = {"action": "unknown", "text": "", "amount": None, "category": None, "items": [], "date": None,
            "time": None, "place": None, "list_name": None}
    return openai_reply({**base, **kw})


def test_natural_language():
    tomorrow = (today() + timedelta(days=1)).isoformat()
    REPLIES.append(intent(action="expense", text="öğle yemeği", amount=250, category="Yemek"))
    send(text="öğlen 250 lira yemeğe verdim")
    card = last("sendMessage")
    assert "Harcama" in card["text"] and "250 ₺" in card["text"] and datas(card) == ["ai:ok", "ai:alt", "ai:x"]
    press("ai:ok")
    assert one("SELECT amount, category, note FROM expenses ORDER BY id DESC LIMIT 1")[:] == (250, "Yemek", "öğle yemeği")
    REPLIES.append(intent(action="todo", text="Dişçiyi ara", date=tomorrow, time="15:00"))
    send(text="yarın 3'te dişçiyi ara")
    press("ai:ok")
    item = one("SELECT * FROM list_items WHERE text = 'Dişçiyi ara'")
    assert (item["due_date"], item["due_time"], item["remind_before"]) == (tomorrow, "15:00", 0)
    REPLIES.append(intent(action="appointment", text="Diş kontrolü", date=tomorrow, time="14:30", place="ADSM"))
    send(text="yarın 14:30 ADSM'de diş kontrolüm var")
    press("ai:ok")
    appt = one("SELECT * FROM appointments ORDER BY id DESC LIMIT 1")
    assert (appt["title"], appt["place"], appt["starts_at"]) == ("Diş kontrolü", "ADSM", f"{tomorrow}T14:30")
    REPLIES.append(intent(action="shopping", items=["süt", "ekmek"]))
    send(text="süt ve ekmek almam lazım")
    press("ai:ok")
    assert one("SELECT COUNT(*) AS n FROM list_items WHERE text IN ('süt', 'ekmek')")["n"] == 2
    REPLIES.append(intent(action="note", text="Wifi şifresi kutunun arkasında"))
    send(text="wifi şifresi kutunun arkasında")
    press("ai:ok")
    assert one("SELECT content FROM notes ORDER BY id DESC LIMIT 1")["content"] == "Wifi şifresi kutunun arkasında"
    # Eksik tutarlı harcama -> anlaşılamadı -> klasik butonlar
    REPLIES.append(intent(action="expense", text="bir şey", amount=None))
    send(text="bir şey aldım")
    assert "tx:note" in datas(last("sendMessage"))
    # Başka türlü -> klasik butonlar
    REPLIES.append(intent(action="note", text="test"))
    send(text="test")
    press("ai:alt")
    assert "tx:todo" in datas(last("sendMessage"))
    # Sağlayıcı hatası -> klasik butonlar
    REPLIES.append(ai.AIError("HTTP 500"))
    send(text="hata olsun")
    assert "tx:note" in datas(last("sendMessage"))
    print("  natural language OK")


def test_questions():
    REPLIES.extend([intent(action="question", text="bu ay ne kadar harcadım?"), openai_reply("Bu ay 662,30 ₺ harcadın.")])
    send(text="bu ay ne kadar harcadım?")
    assert last("sendMessage")["text"] == "🤖 Bu ay 662,30 ₺ harcadın."
    snapshot = REQUESTS[-1][2]["messages"][1]["content"]
    assert "Bu ay" in snapshot and "Yemek" in snapshot and "SORU: bu ay ne kadar harcadım?" in snapshot
    REPLIES.append(openai_reply("<b>kalın</b> değil"))
    send(text="/sor markete ne kadar")
    assert "&lt;b&gt;" in last("sendMessage")["text"]  # model çıktısı kaçışlanır
    print("  questions OK")


def test_opt_out_and_dedupe():
    with app.app_context():
        execute("UPDATE users SET ai_enabled = 0 WHERE id = 1")
    before = len(REQUESTS)
    send(photo=[{"file_id": "f4"}])
    assert "ph:ai" not in datas(last("sendMessage"))
    send(text="yarın dişçi")
    assert "tx:note" in datas(last("sendMessage")) and len(REQUESTS) == before  # hiç veri gönderilmedi
    send(text="/sor deneme")
    assert "kapalı" in last("sendMessage")["text"]
    with app.app_context():
        execute("UPDATE users SET ai_enabled = 1 WHERE id = 1")
    # Aynı güncelleme iki kez gelirse bir kez işlenir
    REPLIES.append(intent(action="note", text="tek sefer"))
    upd = {"update_id": 99999, "message": {"message_id": 1, "chat": {"id": 100}, "text": "tek sefer"}}
    for _ in range(2):
        RAW.post("/telegram/webhook", data=json.dumps(upd), content_type="application/json",
                 headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert len(REQUESTS) == before + 1
    print("  opt-out / dedupe OK")


def test_web():
    c = Client(app)
    h = c.text("/ayarlar/")
    assert "Yapay zekâ" in h and "Mistral AI" in h and "mistral-medium-latest" in h
    c.post("/ayarlar/yapay-zeka", data={})
    assert one("SELECT ai_enabled FROM users WHERE id = 1")["ai_enabled"] == 0
    assert "Fişten ekle" not in c.text("/harcamalar/")
    c.post("/ayarlar/yapay-zeka", data={"ai_enabled": "1"})
    assert "Fişten ekle" in c.text("/harcamalar/")
    REPLIES.append(openai_reply({**RECEIPT, "kind": "bill", "due_date": today().isoformat()}))
    r = c.post("/harcamalar/fis", data={"file": (io.BytesIO(png()), "fis.png")}, content_type="multipart/form-data")
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Fiş okundu" in page and 'value="412,30"' in page and "Fatura olarak kaydet" in page
    assert "Yapay zekâ" in c.text("/yonetim/") and "Bağlantıyı test et" in c.text("/yonetim/")
    REPLIES.append(openai_reply({"ok": True, "message": "Selam!"}))
    r = c.post("/yonetim/yapay-zeka/test", follow_redirects=True)
    assert "Yapay zekâ çalışıyor" in r.get_data(as_text=True)
    set_env()
    assert "yapay zekâ sağlayıcısı ayarlamamış" in c.text("/ayarlar/")
    print("  web OK")


if __name__ == "__main__":
    test_config()
    test_claude_request()
    test_gemini_preset()
    test_mistral_request()
    test_telegram_receipt()
    test_natural_language()
    test_questions()
    test_opt_out_and_dedupe()
    test_web()
    assert not REPLIES, f"kullanılmayan cevaplar: {REPLIES}"
    print("OK")
