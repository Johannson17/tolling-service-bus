# app_bus.py
import os, json, time, threading, html
from typing import Dict, Any
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import pika
from jsonschema import validate, ValidationError
from schemas import ENVELOPE as ENVELOPE_SCHEMA, SCHEMAS

# =========================
# Config
# =========================
CFG_PATH = os.environ.get("BUS_CONFIG", "config.json")
with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)

if os.environ.get("RABBIT_URL"):
    CFG.setdefault("rabbitmq", {})
    CFG["rabbitmq"]["url"] = os.environ["RABBIT_URL"]

RAB = CFG["rabbitmq"]
TOPO = CFG["topology"]
SERVICE_URL = os.environ.get("BUS_SERVICE_URL", "https://tolling-service-bus.onrender.com")

# =========================
# Rabbit publisher (confirms + mandatory + reconexión)
# =========================
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
                # DLX + exchange principal
                ch.exchange_declare(exchange=TOPO["dlx"], exchange_type="fanout", durable=True)
                ch.exchange_declare(exchange=RAB["exchange"], exchange_type=RAB["exchange_type"], durable=True)
                ttl = int(TOPO.get("ttl_ms", 604800000))
                # Queues + bindings + DLQ
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

# =========================
# Flask HTTP (health + schemas + publish pruebas + docs)
# =========================
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
    """Producción: usar AMQP. Este endpoint es para pruebas/CI o integraciones sin AMQP."""
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

# =========================
# Docs HTML (estético, por módulo, con JSON y ejemplos)
# =========================
def _code(s: str) -> str:
    return "<pre><code>" + html.escape(s).replace("</code>", "&lt;/code&gt;") + "</code></pre>"

def build_docs_html() -> str:
    exchange = html.escape(RAB["exchange"])
    dlx = html.escape(TOPO["dlx"])
    url_hint = "amqps://<USER>:<PASS>@<HOST>/<VHOST>  # usar variable de entorno RABBIT_URL"
    python_pub = f"""import json, pika, os
url = os.environ.get("RABBIT_URL")  # setear en el entorno
params = pika.URLParameters(url)
conn = pika.BlockingConnection(params)
ch = conn.channel()
ch.confirm_delivery()
def on_return(ch, method, props, body): print("UNROUTABLE", method.routing_key)
ch.add_on_return_callback(on_return)

envelope = {{...}}  # ver catálogo por evento
rk = envelope["event"]  # regla: routing_key == event
ch.basic_publish("{exchange}", rk, json.dumps(envelope).encode(),
                 pika.BasicProperties(content_type="application/json", delivery_mode=2),
                 mandatory=True)
conn.close()"""

    python_consume_tpl = f"""import pika, json, os
url = os.environ.get("RABBIT_URL")
params = pika.URLParameters(url)
conn = pika.BlockingConnection(params)
ch = conn.channel()
ch.basic_qos(prefetch_count=100)

# Declarar/bindear si hace falta (idempotente)
ch.exchange_declare(exchange="{exchange}", exchange_type="topic", durable=True)
ch.exchange_declare(exchange="{dlx}", exchange_type="fanout", durable=True)
ch.queue_declare(queue="<QUEUE_NAME>", durable=True, arguments={{"x-dead-letter-exchange":"{dlx}","x-message-ttl":604800000}})
ch.queue_bind(queue="<QUEUE_NAME>", exchange="{exchange}", routing_key="<BINDING_KEY>")

def on_msg(ch, method, props, body):
    try:
        msg = json.loads(body)
        # procesar...
        ch.basic_ack(method.delivery_tag)
    except Exception:
        ch.basic_nack(method.delivery_tag, requeue=False)  # va a DLQ

ch.basic_consume(queue="<QUEUE_NAME>", on_message_callback=on_msg)
ch.start_consuming()"""

    examples = {
        "transit.recorded": {
            "event":"transit.recorded","version":"1.0",
            "data":{"transit_id":"t-001","toll_id":"N1","toll_name":"Peaje Norte","lane":"3",
                    "vehicle_id":"V-ABC123","vehicle_type":"car","timestamp":"2025-10-26T14:22:33Z"},
            "meta":{"occurred_at":"2025-10-26T14:22:33Z","producer":"module3","correlation_id":"corr-1"}
        },
        "plate.captured": {
            "event":"plate.captured","version":"1.0",
            "data":{"toll_id":"N1","lane":"3","image_id":"img-99","plate":"ABC123","confidence":0.97,
                    "timestamp":"2025-10-26T14:22:35Z"},
            "meta":{"occurred_at":"2025-10-26T14:22:35Z","producer":"module3"}
        },
        "toll.status.updated": {
            "event":"toll.status.updated","version":"1.0",
            "data":{"toll_id":"N1","toll_name":"Peaje Norte","open_lanes":5,"closed_lanes":1,
                    "timestamp":"2025-10-26T14:30:00Z","alerts":[{"type":"maintenance","lane":"6","time":"2025-10-26T14:29:00Z"}]},
            "meta":{"occurred_at":"2025-10-26T14:30:00Z","producer":"module3"}
        },
        "rate.updated": {
            "event":"rate.updated","version":"1.0",
            "data":{"rate_id":"r-2025-10","category_id":"car","peak_price":1200,"offpeak_price":900,
                    "valid_from":"2025-10-01","valid_to":"2026-03-31"},
            "meta":{"occurred_at":"2025-10-20T10:00:00Z","producer":"module2"}
        },
        "vehicle.category.changed": {
            "event":"vehicle.category.changed","version":"1.0",
            "data":{"vehicle_id":"V-ABC123","old_category_id":"car","new_category_id":"truck",
                    "timestamp":"2025-10-25T08:00:00Z"},
            "meta":{"occurred_at":"2025-10-25T08:00:00Z","producer":"module2"}
        },
        "payment.recorded": {
            "event":"payment.recorded","version":"1.0",
            "data":{"payment_id":"p-1001","toll_id":"N1","toll_name":"Peaje Norte","cashier_id":"c-7",
                    "timestamp":"2025-10-26T15:00:00Z","payment_method":"cash","amount":1200.0,"reason":"toll"},
            "meta":{"occurred_at":"2025-10-26T15:00:00Z","producer":"module4"}
        },
        "prepaid.balance.updated": {
            "event":"prepaid.balance.updated","version":"1.0",
            "data":{"account_id":"acc-1","vehicle_id":"V-ABC123","delta":-1200.0,"balance":5300.0,
                    "timestamp":"2025-10-26T15:00:01Z","source":"auto-charge"},
            "meta":{"occurred_at":"2025-10-26T15:00:01Z","producer":"module4"}
        },
        "debt.created": {
            "event":"debt.created","version":"1.0",
            "data":{"debt_id":"d-42","vehicle_id":"V-XYZ987","amount":25000.0,"origin":"evasion",
                    "timestamp":"2025-10-26T16:05:00Z"},
            "meta":{"occurred_at":"2025-10-26T16:05:00Z","producer":"module4"}
        },
        "debt.settled": {
            "event":"debt.settled","version":"1.0",
            "data":{"debt_id":"d-42","vehicle_id":"V-XYZ987","amount":25000.0,"timestamp":"2025-10-27T11:00:00Z"},
            "meta":{"occurred_at":"2025-10-27T11:00:00Z","producer":"module4"}
        },
        "fine.issued": {
            "event":"fine.issued","version":"1.0",
            "data":{"fine_id":"f-9","vehicle_id":"V-ABC123","timestamp":"2025-10-26T16:00:00Z",
                    "amount":25000.0,"infraction_type":"evasion","state":"open"},
            "meta":{"occurred_at":"2025-10-26T16:00:00Z","producer":"module5"}
        },
        "customer.upserted": {
            "event":"customer.upserted","version":"1.0",
            "data":{"customer_id":"C-77","name":"Acme SA","is_active": True},
            "meta":{"occurred_at":"2025-10-20T09:00:00Z","producer":"module6"}
        },
        "vehicle.upserted": {
            "event":"vehicle.upserted","version":"1.0",
            "data":{"vehicle_id":"V-ABC123","plate":"ABC123","category_id":"car"},
            "meta":{"occurred_at":"2025-10-20T09:05:00Z","producer":"module6"}
        },
        "audit.logged": {
            "event":"audit.logged","version":"1.0",
            "data":{"event_id":"a-1","event_type":"incident","timestamp":"2025-10-26T14:22:33Z",
                    "toll_name":"Peaje Norte","details":"lane 6 closed","vehicle_id": None},
            "meta":{"occurred_at":"2025-10-26T14:22:33Z","producer":"module3"}
        }
    }

    def ex(name: str) -> str:
        return _code(json.dumps(examples[name], ensure_ascii=False, indent=2))

    # Secciones por módulo
    mod = {
        "Módulo 2 — Tarifas y categorías": {
            "cola": "module2.rates.q",
            "recibe": ["vehicle.upserted"],
            "publica": ["rate.updated", "vehicle.category.changed"],
            "bindings": ["rate.*", "vehicle.category.*", "audit.*"]
        },
        "Módulo 3 — Operación": {
            "cola": "module3.ops.q",
            "recibe": ["rate.updated", "vehicle.category.changed", "vehicle.upserted"],
            "publica": ["transit.recorded", "plate.captured", "toll.status.updated"],
            "bindings": ["transit.*", "toll.*", "plate.*", "audit.*"]
        },
        "Módulo 4 — Pagos, prepago y deuda": {
            "cola": "module4.payments.q",
            "recibe": ["transit.recorded", "rate.updated", "vehicle.upserted"],
            "publica": ["payment.recorded", "prepaid.balance.updated", "debt.created", "debt.settled"],
            "bindings": ["payment.*", "debt.*", "prepaid.*", "audit.*"]
        },
        "Módulo 5 — Multas": {
            "cola": "module5.fines.q",
            "recibe": ["plate.captured", "transit.recorded", "vehicle.upserted"],
            "publica": ["fine.issued"],
            "bindings": ["fine.*", "audit.*"]
        },
        "Módulo 6 — Maestros (clientes/vehículos)": {
            "cola": "module6.external.q",
            "recibe": ["prepaid.balance.updated"],
            "publica": ["customer.upserted", "vehicle.upserted"],
            "bindings": ["customer.*", "vehicle.upserted", "prepaid.balance.*"]
        },
        "Módulo 7 — Dashboard": {
            "cola": "module7.dashboard.q",
            "recibe": ["#"],
            "publica": [],
            "bindings": ["#"]
        },
        "Auditoría (transversal)": {
            "cola": "audit.q",
            "recibe": ["audit.logged"],
            "publica": ["audit.logged"],
            "bindings": ["audit.*"]
        }
    }

    css = """
    body{font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;margin:0;background:#0b1020;color:#e5e7eb}
    a{color:#8ab4ff;text-decoration:none} a:hover{text-decoration:underline}
    .layout{display:grid;grid-template-columns:280px 1fr;min-height:100vh}
    .side{background:#0a0f1c;border-right:1px solid #1f2a44;padding:24px;position:sticky;top:0;height:100vh;overflow:auto}
    .brand{font-weight:800;font-size:18px;margin-bottom:16px}
    .muted{color:#9aa4bd}
    .nav a{display:block;padding:8px 0;color:#c8d1ea}
    .main{padding:32px 40px}
    h1{font-size:28px;margin:0 0 12px 0}
    h2{font-size:22px;margin-top:28px;border-bottom:1px solid #1f2a44;padding-bottom:6px}
    h3{font-size:18px;margin-top:20px}
    .grid{display:grid;gap:16px}
    .cols-2{grid-template-columns:repeat(2,minmax(0,1fr))}
    .card{background:#0f1629;border:1px solid #1f2a44;border-radius:12px;padding:16px}
    .kbd{background:#0b1020;border:1px solid #1f2a44;border-radius:6px;padding:2px 6px;font-family:ui-monospace}
    pre{background:#0b1020;border:1px solid #1f2a44;border-radius:12px;padding:12px;overflow:auto}
    code{font-family:ui-monospace,Consolas,Menlo,monospace}
    .badge{display:inline-block;background:#112349;border:1px solid #1f2a44;border-radius:999px;padding:2px 10px;margin-right:6px;color:#a6b2d7}
    .kv{display:grid;grid-template-columns:180px 1fr;gap:8px;margin:8px 0}
    .kv div:first-child{color:#a6b2d7}
    .hr{height:1px;background:#1f2a44;margin:24px 0}
    .small{font-size:12px;color:#9aa4bd}
    .toc h3{margin-top:8px}
    """
    header = f"""
    <div class="layout">
      <div class="side">
        <div class="brand">Tolling RabbitMQ Bus</div>
        <div class="small muted">Realtime entre módulos por AMQP</div>
        <div class="hr"></div>
        <div class="toc">
          <a href="#overview">1. Overview</a>
          <a href="#conexion">2. Conexión</a>
          <a href="#topologia">3. Topología</a>
          <a href="#envelope">4. Envelope</a>
          <a href="#catalogo">5. Catálogo de eventos</a>
          <a href="#modulos">6. Guía por módulo</a>
          <a href="#pruebas">7. Pruebas y utilitarios</a>
          <a href="#reglas">8. Reglas de operación</a>
        </div>
      </div>
      <div class="main">
        <h1>Documento funcional — Bus RabbitMQ</h1>
        <div class="small muted">Webservice utilitario: <a href="{html.escape(SERVICE_URL)}">{html.escape(SERVICE_URL)}</a> · Health/Docs/Publish de prueba</div>
    """

    overview = f"""
    <h2 id="overview">1. Overview</h2>
    <div class="grid cols-2">
      <div class="card">
        <h3>Propósito</h3>
        <div>Intercambio de eventos en tiempo real entre módulos. Todo lo no realtime va por API/BD.</div>
      </div>
      <div class="card">
        <h3>Canales</h3>
        <div><span class="badge">AMQP (productivo)</span> <span class="badge">HTTP (pruebas/edge)</span></div>
      </div>
    </div>
    """

    conexion = f"""
    <h2 id="conexion">2. Conexión</h2>
    <div class="kv"><div>Variable de entorno</div><div><code>RABBIT_URL</code></div></div>
    <div class="kv"><div>Formato</div><div><code>{html.escape(url_hint)}</code></div></div>
    <div class="kv"><div>TLS</div><div>Usar <code>amqps://</code> en producción</div></div>
    <div class="kv"><div>Webservice</div><div><a href="{html.escape(SERVICE_URL)}">{html.escape(SERVICE_URL)}</a> · <code>/health</code>, <code>/schemas</code>, <code>/bus/publish</code></div></div>
    """

    topologia = f"""
    <h2 id="topologia">3. Topología</h2>
    <div class="grid cols-2">
      <div class="card">
        <div class="kv"><div>Exchange</div><div><code>{exchange}</code> (topic)</div></div>
        <div class="kv"><div>DLX</div><div><code>{dlx}</code></div></div>
        <div class="kv"><div>TTL mensajes</div><div>7 días</div></div>
        <div class="kv"><div>Regla</div><div><code>routing_key == event</code></div></div>
      </div>
      <div class="card">
        <h3>Colas por defecto</h3>
        <div class="small">Los equipos pueden crear colas propias y bindear según necesidad</div>
        <ul>
          <li><code>module2.rates.q</code> ← <code>rate.*</code>, <code>vehicle.category.*</code>, <code>audit.*</code></li>
          <li><code>module3.ops.q</code> ← <code>transit.*</code>, <code>toll.*</code>, <code>plate.*</code>, <code>audit.*</code></li>
          <li><code>module4.payments.q</code> ← <code>payment.*</code>, <code>debt.*</code>, <code>prepaid.*</code>, <code>audit.*</code></li>
          <li><code>module5.fines.q</code> ← <code>fine.*</code>, <code>audit.*</code></li>
          <li><code>module6.external.q</code> ← <code>customer.*</code>, <code>vehicle.upserted</code>, <code>prepaid.balance.*</code></li>
          <li><code>module7.dashboard.q</code> ← <code>#</code> (todo)</li>
          <li><code>audit.q</code> ← <code>audit.*</code></li>
        </ul>
      </div>
    </div>
    """

    envelope = f"""
    <h2 id="envelope">4. Envelope</h2>
    {_code(json.dumps({"event":"<event-name>","version":"1.0","data":{"...":"..."},"meta":{"occurred_at":"2025-10-26T14:22:33Z","producer":"moduleX","correlation_id":"uuid","causation_id":"uuid"}}, indent=2))}
    """

    catalogo = """
    <h2 id="catalogo">5. Catálogo de eventos</h2>
    <div class="grid cols-2">
      <div class="card"><h3>Operación (M3)</h3><div><code>transit.recorded</code>, <code>plate.captured</code>, <code>toll.status.updated</code></div></div>
      <div class="card"><h3>Tarifas/Categorías (M2)</h3><div><code>rate.updated</code>, <code>vehicle.category.changed</code></div></div>
      <div class="card"><h3>Pagos/Prepago/Deuda (M4)</h3><div><code>payment.recorded</code>, <code>prepaid.balance.updated</code>, <code>debt.created</code>, <code>debt.settled</code></div></div>
      <div class="card"><h3>Multas (M5)</h3><div><code>fine.issued</code></div></div>
      <div class="card"><h3>Maestros (M6)</h3><div><code>customer.upserted</code>, <code>vehicle.upserted</code></div></div>
      <div class="card"><h3>Auditoría</h3><div><code>audit.logged</code></div></div>
    </div>
    """

    def modulo_html(nombre: str, data: Dict[str, Any]) -> str:
        cola = data["cola"]
        recibe = ", ".join(f"<code>{e}</code>" for e in data["recibe"])
        publica = ", ".join(f"<code>{e}</code>" for e in data["publica"]) if data["publica"] else "<span class='muted'>—</span>"
        binds = ", ".join(f"<code>{b}</code>" for b in data["bindings"])

        ejemplos_pub = ""
        for ev in data["publica"]:
            ejemplos_pub += f"<h4>JSON a publicar — <code>{html.escape(ev)}</code></h4>{_code(json.dumps(examples[ev], ensure_ascii=False, indent=2))}"

        consume_code = python_consume_tpl.replace("<QUEUE_NAME>", cola).replace("<BINDING_KEY>", data["bindings"][0])

        return f"""
        <div class="card">
          <h3>{html.escape(nombre)}</h3>
          <div class="kv"><div>Cola sugerida</div><div><code>{html.escape(cola)}</code></div></div>
          <div class="kv"><div>Bindings</div><div>{binds}</div></div>
          <div class="kv"><div>Recibe</div><div>{recibe}</div></div>
          <div class="kv"><div>Publica</div><div>{publica}</div></div>
          <div class="hr"></div>
          <h4>Publicar (AMQP)</h4>
          {_code(python_pub)}
          {ejemplos_pub if ejemplos_pub else ""}
          <div class="hr"></div>
          <h4>Consumir</h4>
          {_code(consume_code)}
        </div>
        """

    modulos = "<h2 id='modulos'>6. Guía por módulo</h2><div class='grid'>"
    for nombre, data in mod.items():
        modulos += modulo_html(nombre, data)
    modulos += "</div>"

    pruebas = f"""
    <h2 id="pruebas">7. Pruebas y utilitarios</h2>
    <div class="grid cols-2">
      <div class="card">
        <h3>HTTP para pruebas</h3>
        <div><code>POST {html.escape(SERVICE_URL)}/bus/publish</code></div>
        {_code(json.dumps({"routing_key":"payment.recorded","message":examples["payment.recorded"]}, indent=2))}
        <div class="small">Respuesta: 200 publicado · 400 error de validación · 503 Rabbit no disponible</div>
      </div>
      <div class="card">
        <h3>CLI publisher</h3>
        <div class="small">Incluido en el repo como <code>publisher.py</code></div>
        {_code("python publisher.py --file ./samples/payment.recorded.json")}
      </div>
    </div>
    """

    reglas = """
    <h2 id="reglas">8. Reglas de operación</h2>
    <ul>
      <li>Publisher confirms + <code>mandatory</code> para confirmación y detección de <i>unroutable</i>.</li>
      <li>Usar <code>correlation_id</code> como <code>message_id</code> para idempotencia.</li>
      <li><code>ACK</code> en éxito; <code>NACK requeue=false</code> en error → va a DLQ.</li>
      <li>Versionado por <code>version</code>; cambios incompatibles → nueva versión y convivencia.</li>
      <li>Bindings mínimos: filtrar sólo lo necesario; dashboard usa <code>#</code>.</li>
    </ul>
    """

    html_page = f"""<!doctype html><html><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tolling RabbitMQ Bus — Docs</title>
<style>{css}</style>
</head><body>
<div class="layout">
  <div class="side">
    <div class="brand">Tolling RabbitMQ Bus</div>
    <div class="small muted">Realtime entre módulos por AMQP</div>
    <div class="hr"></div>
    <div class="toc">
      <a href="#overview">1. Overview</a>
      <a href="#conexion">2. Conexión</a>
      <a href="#topologia">3. Topología</a>
      <a href="#envelope">4. Envelope</a>
      <a href="#catalogo">5. Catálogo de eventos</a>
      <a href="#modulos">6. Guía por módulo</a>
      <a href="#pruebas">7. Pruebas y utilitarios</a>
      <a href="#reglas">8. Reglas de operación</a>
    </div>
  </div>
  <div class="main">
    <h1>Documento funcional — Bus RabbitMQ</h1>
    <div class="small muted">Webservice utilitario: <a href="{html.escape(SERVICE_URL)}">{html.escape(SERVICE_URL)}</a> · Health/Docs/Publish de prueba</div>
    {overview}
    {conexion}
    {topologia}
    {envelope}
    {catalogo}
    {modulos}
    {pruebas}
    {reglas}
  </div>
</div>
</body></html>"""
    return html_page

@app.get("/docs")
def docs():
    return build_docs_html(), 200, {"Content-Type": "text/html; charset=utf-8"}

if __name__ == "__main__":
    app.run(host=CFG["server"]["host"], port=int(CFG["server"]["port"]), debug=bool(CFG["server"]["debug"]))
