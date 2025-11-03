# smoke_test.py
import os, json, time
from datetime import datetime, timezone
import pika

# URL del módulo 2 (cambiá la pass si la modificaste)
AMQP_URL = os.getenv(
    "AMQP_URL",
    "amqps://module2:m2_Strong_ChangeMe@leopard.lmq.cloudamqp.com/smqbzeze"
)

QUEUE = "module2.rates.q"       # su cola
EXCHANGE = "tolling.bus"        # exchange común
ROUTING_KEY = "rate.updated"    # permitido para module2

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def main():
    params = pika.URLParameters(AMQP_URL)
    params.heartbeat = 30
    conn = pika.BlockingConnection(params)
    ch = conn.channel()

    # Puede declarar su cola (tiene configure sobre su cola)
    ch.queue_declare(queue=QUEUE, durable=True)

    # Publicamos un rate.updated
    msg = {
        "event": ROUTING_KEY,
        "version": "1.0",
        "data": {
            "rate_id": "r-smoketest",
            "category_id": "car",
            "peak_price": 1234,
            "offpeak_price": 987,
            "valid_from": now_iso()[:10],
            "valid_to":   now_iso()[:10]
        },
        "meta": {"occurred_at": now_iso(), "producer": "module2", "correlation_id": "smoke-1"}
    }

    ch.basic_publish(
        exchange=EXCHANGE,
        routing_key=ROUTING_KEY,
        body=json.dumps(msg).encode(),
        properties=pika.BasicProperties(content_type="application/json", delivery_mode=2)
    )
    print("Publicado:", ROUTING_KEY)

    # Buscamos el mensaje en su propia cola (debería rutear por binding rate.*)
    deadline = time.time() + 10  # hasta 10s
    while time.time() < deadline:
        method, props, body = ch.basic_get(queue=QUEUE, auto_ack=False)
        if method:
            print("Recibido de cola:", QUEUE, "| rk:", method.routing_key)
            print(body.decode())
            ch.basic_ack(method.delivery_tag)
            conn.close()
            print("OK smoke test")
            return
        time.sleep(0.5)

    conn.close()
    raise SystemExit("No se recibió el mensaje en 10s (revisá bindings/permisos).")

if __name__ == "__main__":
    main()
