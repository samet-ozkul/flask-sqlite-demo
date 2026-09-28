"""Sağlayıcıdan bağımsız yapay zekâ katmanı (ek paket yok, düz HTTP).

İki bağlantı yöntemi:
- "anthropic": Claude Messages API (/v1/messages)
- "openai":    OpenAI uyumlu Chat Completions (/chat/completions) — OpenAI, Mistral, OpenRouter,
               Groq, DeepSeek, Together, yerel Ollama...

Ortam değişkenleri:
  AI_PROVIDER   anthropic | mistral | openai | openrouter | groq | deepseek | openai-compatible
  AI_API_KEY    sağlayıcının API anahtarı
  AI_MODEL      model adı (anthropic ve mistral için verilmezse varsayılan kullanılır)
  AI_BASE_URL   adres (sadece openai-compatible için gerekli; diğerlerinde isteğe bağlı değiştirme)
  AI_EFFORT     Claude'da düşünme derinliği: low | medium | high (varsayılan medium)

Bütün çağrılar JSON şemasıyla yapılandırılmış çıktı ister; sağlayıcı şemayı desteklemiyorsa
bir kez JSON moduna düşülür.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request

TIMEOUT = 90

PRESETS = {
    "anthropic": {"style": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-opus-5",
                  "label": "Claude (Anthropic)"},
    "mistral": {"style": "openai", "base_url": "https://api.mistral.ai/v1", "model": "mistral-medium-latest",
                "label": "Mistral AI"},
    "openai": {"style": "openai", "base_url": "https://api.openai.com/v1", "model": None, "label": "OpenAI"},
    "openrouter": {"style": "openai", "base_url": "https://openrouter.ai/api/v1", "model": None, "label": "OpenRouter"},
    "groq": {"style": "openai", "base_url": "https://api.groq.com/openai/v1", "model": None, "label": "Groq"},
    "deepseek": {"style": "openai", "base_url": "https://api.deepseek.com/v1", "model": None, "label": "DeepSeek"},
    "openai-compatible": {"style": "openai", "base_url": None, "model": None, "label": "OpenAI uyumlu"},
}

# Claude'da sunucu tarafı yedek model (reddedilen isteği uygun modelle yeniden çalıştırır)
CLAUDE_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5-1")
# output_config.effort kabul eden Claude modelleri (Haiku 4.5 gibi eski modeller reddeder)
CLAUDE_EFFORT_PREFIXES = ("claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-opus-4-8",
                          "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")


class AIError(Exception):
    pass


def config():
    """Etkin ayarlar ya da None (eksik ayar varsa yapay zekâ kapalı sayılır)."""
    provider = os.environ.get("AI_PROVIDER", "").strip().lower()
    key = os.environ.get("AI_API_KEY", "").strip()
    preset = PRESETS.get(provider)
    if not preset or not key:
        return None
    model = os.environ.get("AI_MODEL", "").strip() or preset["model"]
    base_url = (os.environ.get("AI_BASE_URL", "").strip() or preset["base_url"] or "").rstrip("/")
    if not model or not base_url:
        return None
    return {"provider": provider, "style": preset["style"], "label": preset["label"], "key": key,
            "model": model, "base_url": base_url, "effort": os.environ.get("AI_EFFORT", "medium").strip() or "medium"}


def enabled():
    return config() is not None


def missing_reason():
    """Yönetim sayfası için: neden kapalı?"""
    provider = os.environ.get("AI_PROVIDER", "").strip().lower()
    if not provider:
        return "AI_PROVIDER ayarlı değil."
    if provider not in PRESETS:
        return f"Bilinmeyen AI_PROVIDER: {provider}. Seçenekler: {', '.join(PRESETS)}."
    if not os.environ.get("AI_API_KEY", "").strip():
        return "AI_API_KEY ayarlı değil."
    if not (os.environ.get("AI_MODEL", "").strip() or PRESETS[provider]["model"]):
        return f"{PRESETS[provider]['label']} için AI_MODEL gerekli."
    if not (os.environ.get("AI_BASE_URL", "").strip() or PRESETS[provider]["base_url"]):
        return "openai-compatible için AI_BASE_URL gerekli."
    return None


# ---------- HTTP ----------
def _post(url, headers, body):
    """JSON gönderir, JSON döner. HTTP hatasında (durum, gövde) ile AIError."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        err = AIError(f"HTTP {e.code}: {detail}")
        err.status = e.code
        raise err
    except Exception as e:  # zaman aşımı, bağlantı
        raise AIError(f"Bağlantı hatası: {e}")


def _extract_json(text):
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
    raise AIError("Model geçerli JSON döndürmedi.")


# ---------- Claude ----------
def _anthropic(cfg, system, text, schema, image, max_tokens):
    content = []
    if image:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode(image).decode("ascii")}})
    content.append({"type": "text", "text": text})
    body = {"model": cfg["model"], "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": content}]}
    output_config = {}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    if cfg["model"].startswith(CLAUDE_EFFORT_PREFIXES):
        output_config["effort"] = cfg["effort"]
    if output_config:
        body["output_config"] = output_config
    headers = {"x-api-key": cfg["key"], "anthropic-version": "2023-06-01"}
    if cfg["model"] in CLAUDE_FALLBACK_MODELS:
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
    data = _post(f"{cfg['base_url']}/v1/messages", headers, body)
    if data.get("stop_reason") == "refusal":
        raise AIError("Model bu isteği yanıtlamayı reddetti.")
    if data.get("stop_reason") == "max_tokens":
        raise AIError("Yanıt yarıda kesildi (max_tokens).")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


# ---------- OpenAI uyumlu ----------
def _openai(cfg, system, text, schema, image, max_tokens):
    if image:
        data_url = "data:image/jpeg;base64," + base64.standard_b64encode(image).decode("ascii")
        user_content = [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": data_url}}]
    else:
        user_content = text
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_content}]
    body = {"model": cfg["model"], "messages": messages}
    headers = {"Authorization": f"Bearer {cfg['key']}"}
    url = f"{cfg['base_url']}/chat/completions"
    if schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "cevap", "schema": schema, "strict": True}}
    try:
        data = _post(url, headers, body)
    except AIError as e:
        # Şemayı desteklemeyen sağlayıcı/model: bir kez JSON moduna düş, şemayı metinde ver
        if not schema or getattr(e, "status", None) not in (400, 422):
            raise
        body["response_format"] = {"type": "json_object"}
        body["messages"] = [{"role": "system", "content": system + "\n\nSadece şu JSON şemasına uyan bir JSON nesnesi döndür:\n"
                             + json.dumps(schema, ensure_ascii=False)}, messages[1]]
        data = _post(url, headers, body)
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    if isinstance(content, list):  # bazı sağlayıcılar parça listesi döner
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    if choice.get("finish_reason") == "length":
        raise AIError("Yanıt yarıda kesildi (uzunluk sınırı).")
    return content or ""


# ---------- Genel arayüz ----------
def _call(system, text, schema=None, image=None, max_tokens=16000):
    cfg = config()
    if cfg is None:
        raise AIError("Yapay zekâ ayarlı değil.")
    handler = _anthropic if cfg["style"] == "anthropic" else _openai
    return handler(cfg, system, text, schema, image, max_tokens)


def complete_json(system, text, schema, image=None):
    """Şemaya uyan dict döner. image: JPEG baytları."""
    return _extract_json(_call(system, text, schema, image))


def complete_text(system, text):
    out = _call(system, text).strip()
    if not out:
        raise AIError("Boş yanıt.")
    return out
