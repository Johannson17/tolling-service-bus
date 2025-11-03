# app_bus.py
import os, json, time, threading, html
from typing import Dict, Any
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import pika
from jsonschema import validate, ValidationError

# ====== Schemas (envelope + eventos) ======
ENVELOPE_SCHEMA = {
    "type": "object",
    "required": ["event", "version", "data", "meta"],
    "properties": {
        "event": {"type": "string"},
        "version": {"type": "string"},
        "data": {"type": "object"},
        "meta": {
            "type": "object",
            "required": ["occurred_at", "producer"],
            "properties": {
                "occurred_at": {"type": "string", "format": "date-time"},
                "producer": {"type": "string"},
                "correlation_id": {"type": "string"},
                "causation_id": {"type": "string"}
            },
            "additionalProperties": True
        }
    },
    "additionalProperties": False
}

SCHEMAS = {
    "transit.recorded": {
        "type":"object",
        "required":["transit_id","toll_id","toll_name","lane","vehicle_id","vehicle_type","timestamp"],
        "properties":{
            "transit_id":{"type":"string"},
            "toll_id":{"type":"string"},
            "toll_name":{"type":"string"},
            "lane":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "vehicle_type":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"},
            "details":{"type":"string"}
        },
        "additionalProperties": False
    },
    "plate.captured": {
        "type":"object",
        "required":["toll_id","lane","image_id","plate","confidence","timestamp"],
        "properties":{
            "toll_id":{"type":"string"},
            "lane":{"type":"string"},
            "image_id":{"type":"string"},
            "plate":{"type":"string"},
            "confidence":{"type":"number"},
            "timestamp":{"type":"string","format":"date-time"}
        },
        "additionalProperties": False
    },
    "toll.status.updated": {
        "type":"object",
        "required":["toll_id","toll_name","open_lanes","closed_lanes","timestamp"],
        "properties":{
            "toll_id":{"type":"string"},
            "toll_name":{"type":"string"},
            "open_lanes":{"type":"integer"},
            "closed_lanes":{"type":"integer"},
            "timestamp":{"type":"string","format":"date-time"},
            "alerts":{
                "type":"array",
                "items":{
                    "type":"object",
                    "required":["type","time"],
                    "properties":{
                        "type":{"type":"string"},
                        "lane":{"type":["string","null"]},
                        "time":{"type":"string","format":"date-time"}
                    }
                }
            }
        },
        "additionalProperties": False
    },
    "rate.updated": {
        "type":"object",
        "required":["rate_id","category_id","peak_price","offpeak_price","valid_from","valid_to"],
        "properties":{
            "rate_id":{"type":"string"},
            "category_id":{"type":"string"},
            "peak_price":{"type":"number"},
            "offpeak_price":{"type":"number"},
            "valid_from":{"type":"string","format":"date"},
            "valid_to":{"type":"string","format":"date"}
        },
        "additionalProperties": False
    },
    "vehicle.category.changed": {
        "type":"object",
        "required":["vehicle_id","old_category_id","new_category_id","timestamp"],
        "properties":{
            "vehicle_id":{"type":"string"},
            "old_category_id":{"type":"string"},
            "new_category_id":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"}
        },
        "additionalProperties": False
    },
    "payment.recorded": {
        "type":"object",
        "required":["payment_id","toll_id","toll_name","cashier_id","timestamp","payment_method","amount","reason"],
        "properties":{
            "payment_id":{"type":"string"},
            "toll_id":{"type":"string"},
            "toll_name":{"type":"string"},
            "cashier_id":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"},
            "payment_method":{"type":"string"},
            "amount":{"type":"number"},
            "reason":{"type":"string"}
        },
        "additionalProperties": False
    },
    "prepaid.balance.updated": {
        "type":"object",
        "required":["account_id","vehicle_id","delta","balance","timestamp","source"],
        "properties":{
            "account_id":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "delta":{"type":"number"},
            "balance":{"type":"number"},
            "timestamp":{"type":"string","format":"date-time"},
            "source":{"type":"string"}
        },
        "additionalProperties": False
    },
    "debt.created": {
        "type":"object",
        "required":["debt_id","vehicle_id","amount","origin","timestamp"],
        "properties":{
            "debt_id":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "amount":{"type":"number"},
            "origin":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"}
        },
        "additionalProperties": False
    },
    "debt.settled": {
        "type":"object",
        "required":["debt_id","vehicle_id","amount","timestamp"],
        "properties":{
            "debt_id":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "amount":{"type":"number"},
            "timestamp":{"type":"string","format":"date-time"}
        },
        "additionalProperties": False
    },
    "fine.issued": {
        "type":"object",
        "required":["fine_id","vehicle_id","timestamp","amount","infraction_type","state"],
        "properties":{
            "fine_id":{"type":"string"},
            "vehicle_id":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"},
            "amount":{"type":"number"},
            "infraction_type":{"type":"string"},
            "state":{"type":"string"},
            "transit_id":{"type":["string","null"]}
        },
        "additionalProperties": False
    },
    "customer.upserted": {
        "type":"object",
        "required":["customer_id","name","is_active"],
        "properties":{
            "customer_id":{"type":"string"},
            "name":{"type":"string"},
            "is_active":{"type":"boolean"}
        },
        "additionalProperties": True
    },
    "vehicle.upserted": {
        "type":"object",
        "required":["vehicle_id","plate","category_id"],
        "properties":{
            "vehicle_id":{"type":"string"},
            "plate":{"type":"string"},
            "category_id":{"type":"string"}
        },
        "additionalProperties": True
    },
    "audit.logged": {
        "type":"object",
        "required":["event_id","event_type","timestamp","toll_name","details"],
        "properties":{
            "event_id":{"type":"string"},
            "event_type":{"type":"string"},
            "timestamp":{"type":"string","format":"date-time"},
            "toll_name":{"type":"string"},
            "details":{"type":"string"},
            "vehicle_id":{"type":["string","null"]}
        },
        "additionalProperties": False
    }
}

# ====== Config ======
CFG_PATH = os.environ.get("BUS_CONFIG", "config.json")
CFG = json.load(open(CFG_PATH, "r", encoding="utf-8"))

if os.environ.get("RABBIT_URL"):
    CFG.setdefault("rabbitmq", {})
    CFG["rabbitmq"]["url"] = os.environ["RABBIT_URL"]

RAB = CFG["rabbitmq"]
TOPO = CFG["topology"]
BROKER = CFG.get("broker", {})
SERVICE_URL = os.environ.get("BUS_SERVICE_URL", "https://tolling-service-bus.onrender.com")

# ====== Rabbit client (para /health y verificación de topología) ======
class RabbitClient:
    def __init__(self):
        self._conn = None
        self._ch = None
        self._lock = threading.Lock()
        self._last_error = None
        self._topology_ready = False

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
                break
            except Exception as e:
                self._last_error = f"connect_failed: {e}"
                time.sleep(min(delay, 15))
                delay *= 2

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

    @property
    def last_error(self):
        return self._last_error

RB = RabbitClient()

# ====== Flask (sólo utilitarios y docs) ======
app = Flask(__name__)
CORS(app, resources={r"*": {"origins": CFG["server"].get("cors_allow_origins", ["*"])}})

@app.get("/")
def root():
    return redirect("/docs", code=302)

@app.get("/health")
def health():
    ok = RB.ensure_topology()
    return jsonify({"ok": ok, "error": RB.last_error, "topology_ready": ok}), (200 if ok else 503)

@app.get("/schemas")
def list_schemas():
    return jsonify({"events": list(SCHEMAS.keys()), "envelope": ENVELOPE_SCHEMA})

# “Pantalla” de credenciales: los módulos NO ven la URL en la web, la obtienen por este endpoint.
@app.get("/amqp/connection")
def amqp_connection():
    if not BROKER.get("expose_via_api", True):
        return jsonify({"error": "disabled"}), 403
    team = request.args.get("team", "").strip()
    token = request.args.get("token", "").strip()
    team_cfg = (BROKER.get("teams") or {}).get(team)
    if not team_cfg:
        return jsonify({"error": "unknown_team"}), 404
    if token != team_cfg.get("token"):
        return jsonify({"error": "unauthorized"}), 401
    # Opcional: aquí podrías devolver variantes por team (vhost/credenciales separados).
    return jsonify({
        "amqp_url": RAB["url"],
        "exchange": RAB["exchange"],
        "exchange_type": RAB["exchange_type"]
    })

@app.get("/openapi.json")
def openapi_json():
    # Minimal: sólo utilitarios
    return {
        "openapi": "3.0.3",
        "info": {"title": "Tolling RabbitMQ Bus (Utilities)", "version": "1.0.0"},
        "paths": {
            "/health": {"get": {"summary": "Health"}},
            "/schemas": {"get": {"summary": "List Rabbit event schemas"}},
            "/amqp/connection": {"get": {"summary": "Issue AMQP URL per team (token required)"}}
        }
    }

# ====== Docs (solo Rabbit, por módulo, con ejemplos) ======
def _code(s: str) -> str:
    return "<pre><code>" + html.escape(s) + "</code></pre>"

def build_docs_html() -> str:
    exchange = html.escape(RAB["exchange"])
    dlx = html.escape(TOPO["dlx"])
    python_pub = f"""import os, json, pika, requests

# 1) obtener URL AMQP desde el broker (esta app)
resp = requests.get("{SERVICE_URL}/amqp/connection", params={{"team":"module3","token":"m3token"}})
resp.raise_for_status()
amqp = resp.json()["amqp_url"]

# 2) publicar a Rabbit
params = pika.URLParameters(amqp)
conn = pika.BlockingConnection(params)
ch = conn.channel()
ch.confirm_delivery()

envelope = {{
  "event":"transit.recorded",
  "version":"1.0",
  "data":{{"transit_id":"t-001","toll_id":"N1","toll_name":"Peaje Norte","lane":"3","vehicle_id":"V-ABC123","vehicle_type":"car","timestamp":"2025-10-26T14:22:33Z"}},
  "meta":{{"occurred_at":"2025-10-26T14:22:33Z","producer":"module3","correlation_id":"corr-1"}}
}}

rk = envelope["event"]  # regla: routing_key == event
ch.basic_publish("{exchange}", rk, json.dumps(envelope).encode(),
                 pika.BasicProperties(content_type="application/json", delivery_mode=2),
                 mandatory=True)
conn.close()"""

    python_consume_tpl = f"""import os, json, pika, requests

# 1) obtener URL AMQP desde el broker
resp = requests.get("{SERVICE_URL}/amqp/connection", params={{"team":"<TEAM>","token":"<TOKEN>"}})
resp.raise_for_status()
amqp = resp.json()["amqp_url"]

# 2) consumir
params = pika.URLParameters(amqp)
conn = pika.BlockingConnection(params)
ch = conn.channel()
ch.basic_qos(prefetch_count=100)

# idempotente
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
        ch.basic_nack(method.delivery_tag, requeue=False)  # DLQ

ch.basic_consume(queue="<QUEUE_NAME>", on_message_callback=on_msg)
ch.start_consuming()"""

    # ejemplos
    examples = {
        "transit.recorded": {
            "event":"transit.recorded","version":"1.0",
            "data":{"transit_id":"t-001","toll_id":"N1","toll_name":"Peaje Norte","lane":"3",
                    "vehicle_id":"V-ABC123","vehicle_type":"car","timestamp":"2025-10-26T14:22:33Z"},
            "meta":{"occurred_at":"2025-10-26T14:22:33Z","producer":"module3","correlation_id":"corr-1"}
        },
        "payment.recorded": {
            "event":"payment.recorded","version":"1.0",
            "data":{"payment_id":"p-1001","toll_id":"N1","toll_name":"Peaje Norte","cashier_id":"c-7",
                    "timestamp":"2025-10-26T15:00:00Z","payment_method":"cash","amount":1200.0,"reason":"toll"},
            "meta":{"occurred_at":"2025-10-26T15:00:00Z","producer":"module4"}
        },
        "customer.upserted": {
            "event":"customer.upserted","version":"1.0",
            "data":{"customer_id":"C-77","name":"Acme SA","is_active": True},
            "meta":{"occurred_at":"2025-10-20T09:00:00Z","producer":"module6"}
        }
    }

    def ex(name: str) -> str:
        return _code(json.dumps(examples[name], ensure_ascii=False, indent=2))

    # guía por módulo
    mod = {
        "Módulo 2 — Tarifas y categorías": {
            "cola": "module2.rates.q",
            "recibe": ["vehicle.upserted"],
            "publica": ["rate.updated", "vehicle.category.changed"],
            "bindings": ["rate.*", "vehicle.category.*", "audit.*"],
            "ejemplo_pub": ["vehicle.category.changed"]
        },
        "Módulo 3 — Operación": {
            "cola": "module3.ops.q",
            "recibe": ["rate.updated", "vehicle.category.changed", "vehicle.upserted"],
            "publica": ["transit.recorded", "plate.captured", "toll.status.updated"],
            "bindings": ["transit.*", "toll.*", "plate.*", "audit.*"],
            "ejemplo_pub": ["transit.recorded"]
        },
        "Módulo 4 — Pagos, prepago y deuda": {
            "cola": "module4.payments.q",
            "recibe": ["transit.recorded", "rate.updated", "vehicle.upserted"],
            "publica": ["payment.recorded", "prepaid.balance.updated", "debt.created", "debt.settled"],
            "bindings": ["payment.*", "debt.*", "prepaid.*", "audit.*"],
            "ejemplo_pub": ["payment.recorded"]
        },
        "Módulo 5 — Multas": {
            "cola": "module5.fines.q",
            "recibe": ["plate.captured", "transit.recorded", "vehicle.upserted"],
            "publica": ["fine.issued"],
            "bindings": ["fine.*", "audit.*"],
            "ejemplo_pub": []
        },
        "Módulo 6 — Maestros (clientes/vehículos)": {
            "cola": "module6.external.q",
            "recibe": ["prepaid.balance.updated"],
            "publica": ["customer.upserted", "vehicle.upserted"],
            "bindings": ["customer.*", "vehicle.upserted", "prepaid.balance.*"],
            "ejemplo_pub": ["customer.upserted"]
        },
        "Módulo 7 — Dashboard": {
            "cola": "module7.dashboard.q",
            "recibe": ["#"],
            "publica": [],
            "bindings": ["#"],
            "ejemplo_pub": []
        },
        "Auditoría (transversal)": {
            "cola": "audit.q",
            "recibe": ["audit.logged"],
            "publica": ["audit.logged"],
            "bindings": ["audit.*"],
            "ejemplo_pub": []
        }
    }

    css = """
    body{font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;margin:0;background:#0b1020;color:#e5e7eb}
    a{color:#8ab4ff;text-decoration:none} a:hover{text-decoration:underline}
    .layout{display:grid;grid-template-columns:280px 1fr;min-height:100vh}
    .side{background:#0a0f1c;border-right:1px solid #1f2a44;padding:24px;position:sticky;top:0;height:100vh;overflow:auto}
    .brand{font-weight:800;font-size:18px;margin-bottom:16px}
    .muted{color:#9aa4bd}
    .main{padding:32px 40px}
    h1{font-size:28px;margin:0 0 12px 0}
    h2{font-size:22px;margin-top:28px;border-bottom:1px solid #1f2a44;padding-bottom:6px}
    h3{font-size:18px;margin-top:20px}
    .grid{display:grid;gap:16px}
    .cols-2{grid-template-columns:repeat(2,minmax(0,1fr))}
    .card{background:#0f1629;border:1px solid #1f2a44;border-radius:12px;padding:16px}
    pre{background:#0b1020;border:1px solid #1f2a44;border-radius:12px;padding:12px;overflow:auto}
    code{font-family:ui-monospace,Consolas,Menlo,monospace}
    .kv{display:grid;grid-template-columns:200px 1fr;gap:8px;margin:8px 0}
    .kv div:first-child{color:#a6b2d7}
    .hr{height:1px;background:#1f2a44;margin:24px 0}
    .small{font-size:12px;color:#9aa4bd}
    """
    header = f"""
    <div class="layout">
      <div class="side">
        <div class="brand">Tolling RabbitMQ Bus</div>
        <div class="small muted">Realtime entre módulos por AMQP</div>
      </div>
      <div class="main">
        <h1>Documento funcional — Bus RabbitMQ</h1>
        <div class="small muted">Este servicio actúa como pantalla de credenciales. Los módulos obtienen la URL AMQP aquí y se conectan a Rabbit.</div>
    """

    overview = f"""
    <h2>1. Conexión (vía “pantalla”)</h2>
    <div class="card">
      <div class="kv"><div>Endpoint de credenciales</div><div><code>{html.escape(SERVICE_URL)}/amqp/connection?team=&lt;TEAM&gt;&amp;token=&lt;TOKEN&gt;</code></div></div>
      <div class="kv"><div>Respuesta</div><div><code>{{"amqp_url": "...", "exchange": "{exchange}", "exchange_type": "topic"}}</code></div></div>
      <div class="kv"><div>Uso</div><div>Los clientes piden la URL y luego se conectan por AMQP (pika, etc.).</div></div>
    </div>
    """

    topo = f"""
    <h2>2. Topología</h2>
    <div class="grid cols-2">
      <div class="card">
        <div class="kv"><div>Exchange</div><div><code>{exchange}</code> (topic)</div></div>
        <div class="kv"><div>DLX</div><div><code>{dlx}</code></div></div>
        <div class="kv"><div>TTL mensajes</div><div>7 días</div></div>
        <div class="kv"><div>Regla</div><div><code>routing_key == event</code></div></div>
      </div>
      <div class="card">
        <h3>Colas por defecto</h3>
        <ul>
          <li><code>module2.rates.q</code> ← <code>rate.*</code>, <code>vehicle.category.*</code>, <code>audit.*</code></li>
          <li><code>module3.ops.q</code> ← <code>transit.*</code>, <code>toll.*</code>, <code>plate.*</code>, <code>audit.*</code></li>
          <li><code>module4.payments.q</code> ← <code>payment.*</code>, <code>debt.*</code>, <code>prepaid.*</code>, <code>audit.*</code></li>
          <li><code>module5.fines.q</code> ← <code>fine.*</code>, <code>audit.*</code></li>
          <li><code>module6.external.q</code> ← <code>customer.*</code>, <code>vehicle.upserted</code>, <code>prepaid.balance.*</code></li>
          <li><code>module7.dashboard.q</code> ← <code>#</code></li>
          <li><code>audit.q</code> ← <code>audit.*</code></li>
        </ul>
      </div>
    </div>
    """

    env = _code(json.dumps({
        "event":"<event-name>",
        "version":"1.0",
        "data":{"...":"..."},
        "meta":{
            "occurred_at":"2025-10-26T14:22:33Z",
            "producer":"moduleX",
            "correlation_id":"uuid",
            "causation_id":"uuid"
        }
    }, indent=2))

    envelope = f"""
    <h2>3. Envelope</h2>
    {env}
    """

    # ejemplos por módulo
    def bloc_mod(title, cola, bindings, recibe, publica, ejemplo_event=None, example_json=None, team="moduleX", token="token"):
        consume_code = python_consume_tpl.replace("<TEAM>", team).replace("<TOKEN>", token)\
                                         .replace("<QUEUE_NAME>", cola).replace("<BINDING_KEY>", bindings[0])
        pub = ""
        if ejemplo_event and example_json:
            pub = "<h4>Ejemplo de publicación</h4>" + _code(python_pub) + "<h4>Payload</h4>" + _code(json.dumps(example_json, indent=2))
        return f"""
        <div class="card">
          <h3>{html.escape(title)}</h3>
          <div class="kv"><div>Cola sugerida</div><div><code>{html.escape(cola)}</code></div></div>
          <div class="kv"><div>Bindings</div><div>{", ".join(f"<code>{b}</code>" for b in bindings)}</div></div>
          <div class="kv"><div>Recibe</div><div>{", ".join(f"<code>{e}</code>" for e in recibe)}</div></div>
          <div class="kv"><div>Publica</div><div>{(", ".join(f"<code>{e}</code>" for e in publica) or "<span class='small'>—</span>")}</div></div>
          {pub}
          <h4>Consumir</h4>
          { _code(consume_code) }
        </div>
        """

    mod_grid = """
    <h2>4. Guía por módulo</h2>
    <div class="grid">
    """ + \
    bloc_mod("Módulo 2 — Tarifas y categorías",
             "module2.rates.q",
             ["rate.*","vehicle.category.*","audit.*"],
             ["vehicle.upserted"],
             ["rate.updated","vehicle.category.changed"],
             "vehicle.category.changed",
             {"event":"vehicle.category.changed","version":"1.0",
              "data":{"vehicle_id":"V-ABC123","old_category_id":"car","new_category_id":"truck","timestamp":"2025-10-25T08:00:00Z"},
              "meta":{"occurred_at":"2025-10-25T08:00:00Z","producer":"module2"}},
             "module2","m2token"
    ) + \
    bloc_mod("Módulo 3 — Operación",
             "module3.ops.q",
             ["transit.*","toll.*","plate.*","audit.*"],
             ["rate.updated","vehicle.category.changed","vehicle.upserted"],
             ["transit.recorded","plate.captured","toll.status.updated"],
             "transit.recorded",
             {"event":"transit.recorded","version":"1.0",
              "data":{"transit_id":"t-001","toll_id":"N1","toll_name":"Peaje Norte","lane":"3","vehicle_id":"V-ABC123","vehicle_type":"car","timestamp":"2025-10-26T14:22:33Z"},
              "meta":{"occurred_at":"2025-10-26T14:22:33Z","producer":"module3","correlation_id":"corr-1"}},
             "module3","m3token"
    ) + \
    bloc_mod("Módulo 4 — Pagos, prepago y deuda",
             "module4.payments.q",
             ["payment.*","debt.*","prepaid.*","audit.*"],
             ["transit.recorded","rate.updated","vehicle.upserted"],
             ["payment.recorded","prepaid.balance.updated","debt.created","debt.settled"],
             "payment.recorded",
             {"event":"payment.recorded","version":"1.0",
              "data":{"payment_id":"p-1001","toll_id":"N1","toll_name":"Peaje Norte","cashier_id":"c-7","timestamp":"2025-10-26T15:00:00Z","payment_method":"cash","amount":1200.0,"reason":"toll"},
              "meta":{"occurred_at":"2025-10-26T15:00:00Z","producer":"module4"}},
             "module4","m4token"
    ) + \
    bloc_mod("Módulo 5 — Multas",
             "module5.fines.q",
             ["fine.*","audit.*"],
             ["plate.captured","transit.recorded","vehicle.upserted"],
             ["fine.issued"],
             None, None, "module5","m5token"
    ) + \
    bloc_mod("Módulo 6 — Maestros",
             "module6.external.q",
             ["customer.*","vehicle.upserted","prepaid.balance.*"],
             ["prepaid.balance.updated"],
             ["customer.upserted","vehicle.upserted"],
             "customer.upserted",
             {"event":"customer.upserted","version":"1.0",
              "data":{"customer_id":"C-77","name":"Acme SA","is_active": True},
              "meta":{"occurred_at":"2025-10-20T09:00:00Z","producer":"module6"}},
             "module6","m6token"
    ) + \
    bloc_mod("Módulo 7 — Dashboard",
             "module7.dashboard.q",
             ["#"],
             ["#"],
             [],
             None, None, "module7","m7token"
    ) + \
    bloc_mod("Auditoría (transversal)",
             "audit.q",
             ["audit.*"],
             ["audit.logged"],
             ["audit.logged"],
             None, None, "audit","audittoken"
    ) + "</div>"

    html_page = f"""<!doctype html><html><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tolling RabbitMQ Bus — Docs</title>
<style>{css}</style>
</head><body>
{header}
{overview}
{topo}
{envelope}
{mod_grid}
</div></div>
</body></html>"""
    return html_page

@app.get("/docs")
def docs():
    return build_docs_html(), 200, {"Content-Type": "text/html; charset=utf-8"}

if __name__ == "__main__":
    app.run(host=CFG["server"]["host"], port=int(CFG["server"]["port"]), debug=bool(CFG["server"]["debug"]))
