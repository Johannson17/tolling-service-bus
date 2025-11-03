# app_bus.py
import os, json, time, threading
from typing import Dict, Any
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import pika
from jsonschema import validate, ValidationError
from schemas import ENVELOPE as ENVELOPE_SCHEMA, SCHEMAS

# Config
CFG_PATH = os.environ.get("BUS_CONFIG", "config.json")
with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)

if os.environ.get("RABBIT_URL"):
    CFG.setdefault("rabbitmq", {})
    CFG["rabbitmq"]["url"] = os.environ["RABBIT_URL"]

RAB = CFG["rabbitmq"]
TOPO = CFG["topology"]

# Rabbit publisher con confirms/mandatory y reconexión
class RabbitClient:
    def __init__(self):
        self._conn = None
        self._ch = None
        self._lock = threading.Lock()
        self._last_returned = None
        self._topology_ready = False
        self._last_error = None

    def _params(self) -> pika.URLParameters:
        p = pika.URLParameters(RAB["url"])
        p.heartbeat = int(RAB.get("heartbeat", 30))
        p.blocked_connection_timeout = int(RAB.get("blocked_timeout", 60))
        return p

    def _ensure_connection(self):
        if self._conn and self._conn.is_open and self._ch and self._ch.is_open:
            return
        delay = 1.0
        while True:
            try:
                self._conn = pika.BlockingConnection(self._params())
                self._ch = self._conn.channel()
                self._ch.confirm_delivery()
                self._last_returned = None
                self._ch.add_on_return_callback(self._on_return)
                break
            except Exception as e:
                self._last_error = f"connect_failed: {e}"
                time.sleep(min(delay, 15))
                delay *= 2

    def _on_return(self, ch, method, properties, body):
        self._last_returned = {
            "reply_code": method.reply_code,
            "reply_text": method.reply_text,
            "exchange": method.exchange,
            "routing_key": method.routing_key
        }

    def ensure_topology(self) -> bool:
        if self._topology_ready:
            return True
        with self._lock:
            if self._topology_ready:
                return True
            try:
                self._ensure_connection()
                ch = self._ch
                ch.exchange_declare(exchange=TOPO["dlx"], exchange_type="fanout", durable=True)
                ch.exchange_declare(exchange=RAB["exchange"], exchange_type=RAB["exchange_type"], durable=True)
                ttl = int(TOPO.get("ttl_ms", 604800000))
                for q in TOPO["queues"]:
                    args = {"x-dead-letter-exchange": TOPO["dlx"], "x-message-ttl": ttl}
                    ch.queue_declare(queue=q["name"], durable=True, arguments=args)
                    for rk in q["bindings"]:
                        ch.queue_bind(queue=q["name"], exchange=RAB["exchange"], routing_key=rk)
                    dlq = f"{q['name']}.dlq"
                    ch.queue_declare(queue=dlq, durable=True)
                    ch.queue_bind(queue=dlq, exchange=TOPO["dlx"], routing_key="")
                self._topology_ready = True
                self._last_error = None
                return True
            except Exception as e:
                self._last_error = f"topology_failed: {e}"
                return False

    def publish(self, routing_key: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
        validate(envelope, ENVELOPE_SCHEMA)
        evt = envelope["event"]
        schema = SCHEMAS.get(evt)
        if not schema:
            raise ValidationError(f"Schema no registrado para event='{evt}'")
        validate(envelope["data"], schema)

        self._ensure_connection()
        if not self._topology_ready:
            self.ensure_topology()

        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        corr = (envelope.get("meta") or {}).get("correlation_id")
        props = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            message_id=corr if corr else None
        )
        ok = self._ch.basic_publish(
            exchange=RAB["exchange"],
            routing_key=routing_key,
            body=body,
            properties=props,
            mandatory=True
        )
        if self._last_returned:
            err = self._last_returned
            self._last_returned = None
            raise RuntimeError(f"unroutable: {err}")
        if not ok:
            raise RuntimeError("publish not confirmed")
        return {"confirmed": True}

    @property
    def last_error(self):
        return self._last_error

RB = RabbitClient()

# HTTP mínimo
app = Flask(__name__)
CORS(app, resources={r"*": {"origins": CFG["server"].get("cors_allow_origins", ["*"])}})

@app.get("/")
def root():
    return redirect("/docs", code=302)

@app.get("/health")
def health():
    ok = RB.ensure_topology()
    status = 200 if ok else 503
    return jsonify({"ok": ok, "error": RB.last_error, "topology_ready": ok}), status

@app.get("/schemas")
def list_schemas():
    return jsonify({"events": list(SCHEMAS.keys()), "envelope": ENVELOPE_SCHEMA})

@app.post("/bus/publish")
def http_publish():
    body = request.get_json(silent=True) or {}
    msg = body.get("message")
    if not isinstance(msg, dict):
        return jsonify({"error": "message requerido"}), 400
    rk = body.get("routing_key") or msg.get("event")
    if not rk:
        return jsonify({"error": "routing_key/event requerido"}), 400
    if not RB.ensure_topology():
        return jsonify({"error": "rabbit_unavailable", "detail": RB.last_error}), 503
    try:
        res = RB.publish(rk, msg)
        return jsonify({"published": True, "routing_key": rk, **res})
    except ValidationError as ve:
        return jsonify({"error": "validation_error", "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"error": "publish_failed", "message": str(e)}), 500

@app.get("/openapi.json")
def openapi_json():
    return {
        "openapi": "3.0.3",
        "info": {"title": "Tolling RabbitMQ Bus", "version": "1.0.0"},
        "paths": {
            "/health": {"get": {"summary": "Health", "responses": {"200": {"description": "OK"}, "503": {"description": "Unavailable"}}}},
            "/schemas": {"get": {"summary": "List schemas", "responses": {"200": {"description": "OK"}}}},
            "/bus/publish": {"post": {"summary": "Publish (tests/edge only)", "responses": {"200": {"description": "Published"}}}}
        }
    }

# Documento funcional HTML centrado en AMQP (render en /docs)
_FUNCTIONAL_HTML = r"""
<!doctype html><html><head>
<meta charset="utf-8"/>
<title>Tolling RabbitMQ Bus — Documento funcional</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
 body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;margin:24px;line-height:1.45}
 code,pre{font-family:ui-monospace,Consolas,Menlo,monospace;background:#f6f8fa;border-radius:6px}
 pre{padding:12px;overflow:auto}
 .muted{color:#666}
 .box{border:1px solid #e5e7eb;border-radius:8px;padding:12px;margin:12px 0}
</style>
</head><body>
<h1>Tolling RabbitMQ Bus — Documento funcional</h1>
<p class="muted">Producción realtime entre módulos usando RabbitMQ (AMQP). La API no es el canal oficial; sólo health, contratos y pruebas.<br/>
Webservice: <a href="#">(tu URL de despliegue)</a></p>

<h2>Topología</h2>
<div class="box">
<b>Exchange</b>: <code>tolling.bus</code> (topic) ·
<b>DLX</b>: <code>tolling.dlx</code> ·
<b>TTL</b>: 7 días ·
<b>Regla</b>: <code>routing_key == event</code>
</div>
<ul>
<li><code>module2.rates.q</code> ← <code>rate.*</code>, <code>vehicle.category.*</code>, <code>audit.*</code></li>
<li><code>module3.ops.q</code> ← <code>transit.*</code>, <code>toll.*</code>, <code>plate.*</code>, <code>audit.*</code></li>
<li><code>module4.payments.q</code> ← <code>payment.*</code>, <code>debt.*</code>, <code>prepaid.*</code>, <code>audit.*</code></li>
<li><code>module5.fines.q</code> ← <code>fine.*</code>, <code>audit.*</code></li>
<li><code>module6.external.q</code> ← <code>customer.*</code>, <code>vehicle.upserted</code>, <code>prepaid.balance.*</code></li>
<li><code>module7.dashboard.q</code> ← <code>#</code> (todo)</li>
<li><code>audit.q</code> ← <code>audit.*</code></li>
</ul>

<h2>Envelope obligatorio</h2>
<pre>{
  "event": "&lt;event-name&gt;",
  "version": "1.0",
  "data": { /* payload del evento */ },
  "meta": {
    "occurred_at": "2025-10-26T14:22:33Z",
    "producer": "moduleX",
    "correlation_id": "uuid",
    "causation_id": "uuid"
  }
}</pre>

<h2>Publicar (AMQP)</h2>
<pre><code>import json, pika
url = "amqps://smqbzeze:xlgdZM_NyZk8akZuWpUTDWpEb-16G4mf@leopard.lmq.cloudamqp.com/smqbzeze"
params = pika.URLParameters(url)
conn = pika.BlockingConnection(params)
ch = conn.channel()
ch.confirm_delivery()
def on_return(ch, method, props, body): print("UNROUTABLE", method.routing_key)
ch.add_on_return_callback(on_return)

envelope = {...}
rk = envelope["event"]
ch.basic_publish("tolling.bus", rk, json.dumps(envelope).encode(),
                 pika.BasicProperties(content_type="application/json", delivery_mode=2),
                 mandatory=True)
conn.close()</code></pre>

<h2>Consumir</h2>
<pre><code>params = pika.URLParameters("amqps://smqbzeze:***@leopard.lmq.cloudamqp.com/smqbzeze")
conn = pika.BlockingConnection(params)
ch = conn.channel()
ch.basic_qos(prefetch_count=100)
ch.queue_declare(queue="module7.dashboard.q", durable=True)
ch.queue_bind(queue="module7.dashboard.q", exchange="tolling.bus", routing_key="#")
def on_msg(ch, method, props, body):
    try:
        # procesar...
        ch.basic_ack(method.delivery_tag)
    except Exception:
        ch.basic_nack(method.delivery_tag, requeue=False)
ch.basic_consume(queue="module7.dashboard.q", on_message_callback=on_msg)
ch.start_consuming()</code></pre>

<h2>Eventos disponibles</h2>
<ul>
<li>Operación (M3): <code>transit.recorded</code>, <code>plate.captured</code>, <code>toll.status.updated</code></li>
<li>Tarifas/Categorías (M2): <code>rate.updated</code>, <code>vehicle.category.changed</code></li>
<li>Pagos/Prepago/Deuda (M4): <code>payment.recorded</code>, <code>prepaid.balance.updated</code>, <code>debt.created</code>, <code>debt.settled</code></li>
<li>Multas (M5): <code>fine.issued</code></li>
<li>Maestros (M6): <code>customer.upserted</code>, <code>vehicle.upserted</code></li>
<li>Auditoría: <code>audit.logged</code></li>
</ul>
</body></html>
"""

@app.get("/docs")
def docs():
    return _FUNCTIONAL_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

if __name__ == "__main__":
    app.run(host=CFG["server"]["host"], port=int(CFG["server"]["port"]), debug=bool(CFG["server"]["debug"]))
