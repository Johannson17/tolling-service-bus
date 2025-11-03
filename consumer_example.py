# publish_m2_min.py
# deps: pip install requests pika

import os, json, time, requests, pika

# Config de PRUEBA (podés overridear con env vars)
SERVICE_URL = os.getenv("SERVICE_URL", "https://tolling-service-bus.onrender.com")
MODULE_TOKEN = os.getenv("MODULE_TOKEN", "tok-M2-TEST-34cd")
EVENT_ID = os.getenv("EVENT_ID", "EVT-DEMO-1")
PLATE = os.getenv("PLATE", "ABC123")
CONFIDENCE = float(os.getenv("CONFIDENCE", "0.98"))

def get_credentials(service_url: str, token: str) -> dict:
    url = service_url.rstrip("/") + "/auth/credentials"
    r = requests.post(url, json={"token": token}, timeout=20)
    r.raise_for_status()
    return r.json()

def publish(amqp_url: str, exchange: str, routing_key: str, payload: dict) -> None:
    params = pika.URLParameters(amqp_url)
    params.heartbeat = 30
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ch.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
    ch.basic_publish(
        exchange=exchange,
        routing_key=routing_key,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
    )
    print(f"OK published rk={routing_key} payload={payload}")
    conn.close()

if __name__ == "__main__":
    # 1) Obtener credenciales/política con el token del Módulo 2
    creds = get_credentials(SERVICE_URL, MODULE_TOKEN)
    amqp_url = creds["amqp_url"]
    exchange = creds["exchange"]

    # 2) Construir payload de reconocimiento de patente (M2 publica esto)
    payload = {
        "event_id": EVENT_ID,
        "recognized": True,
        "plate": PLATE,
        "format": "mercosur",
        "confidence": CONFIDENCE,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # 3) Publicar en plate.recognition.result
    publish(amqp_url, exchange, "plate.recognition.result", payload)
