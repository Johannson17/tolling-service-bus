import os
import json
import html
from datetime import datetime
from flask import Flask, jsonify, Response
from flask_cors import CORS
import pika

APP_TITLE = "Tolling Service Bus – Docs"
RABBIT_URL = os.getenv("RABBITMQ_URL", "amqps://guest:guest@localhost:5671/%2F")

# Carga de catálogos para docs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_MODULES_PATH = os.path.join(BASE_DIR, "docs_modules.json")
EVENTS_CATALOG_PATH = os.path.join(BASE_DIR, "events_catalog.json")

with open(DOCS_MODULES_PATH, "r", encoding="utf-8") as f:
    DOCS_MODULES = json.load(f)

with open(EVENTS_CATALOG_PATH, "r", encoding="utf-8") as f:
    EVENTS = json.load(f)

# -----------------------------
# Conexión RabbitMQ (solo para declarar topología si lo necesitás)
# -----------------------------
def _params():
    return pika.URLParameters(RABBIT_URL)

def rabbit_connect():
    return pika.BlockingConnection(_params())

def declare_topology():
    """
    Declara exchanges/colas que usemos en el catálogo (idempotente).
    Si tu instancia ya los creó externamente, esto no es obligatorio.
    """
    conn = rabbit_connect()
    ch = conn.channel()
    # Un único exchange topic para todo el bus
    ch.exchange_declare(exchange="tolling.bus", exchange_type="topic", durable=True)

    # Declaración opcional de colas “genéricas demo” por evento, para no fallar si alguien bindea
    for ev_key, ev in EVENTS.items():
        # cola “documental” por si querés testear a mano (se puede borrar en prod real)
        qname = f"doc.{ev_key}"
        ch.queue_declare(queue=qname, durable=True)
        ch.queue_bind(exchange="tolling.bus", queue=qname, routing_key=ev["routing_key"])
    conn.close()

# Si querés que declare en arranque, descomentá:
# declare_topology()

# -----------------------------
# Flask app solo con /health y /docs (sin API de prueba)
# -----------------------------
app = Flask(__name__)
CORS(app)

@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "time": datetime.utcnow().isoformat() + "Z",
        "rabbit_reachable": _ping_rabbit()
    })

def _ping_rabbit():
    try:
        conn = rabbit_connect()
        conn.close()
        return True
    except Exception:
        return False

@app.get("/")
def root():
    return Response(
        '<html><head><meta http-equiv="refresh" content="0; url=/docs"></head><body></body></html>',
        mimetype="text/html"
    )

@app.get("/docs")
def docs():
    return Response(build_docs_html(), mimetype="text/html; charset=utf-8")

# -----------------------------
# Render de documentación
# -----------------------------
def _code(json_obj):
    # Asegura JSON válido con indentación y evita NameError por literales
    return html.escape(json.dumps(json_obj, ensure_ascii=False, indent=2))

def _event_block(ev_key):
    ev = EVENTS[ev_key]
    return f"""
      <div class="event">
        <div class="rk"><b>Routing key</b>: <code>{ev['routing_key']}</code></div>
        <div class="desc">{html.escape(ev['description_es'])}</div>
        <div class="payload">
          <div><b>Payload JSON</b></div>
          <pre>{_code(ev['payload_example'])}</pre>
        </div>
        <div class="notes">{html.escape(ev.get('notes_es',''))}</div>
      </div>
    """

def build_docs_html():
    # Encabezado
    mod_cards = []
    for mod in DOCS_MODULES["modules"]:
        publishes = "\n".join(_event_block(k) for k in mod.get("publishes", []))
        subscribes = "\n".join(_event_block(k) for k in mod.get("subscribes", []))

        mod_cards.append(f"""
          <section class="module">
            <h2>{html.escape(mod['code'])} — {html.escape(mod['name_es'])}</h2>
            <p class="summary">{html.escape(mod['summary_es'])}</p>

            <div class="howto">
              <h3>Cómo conectarse (RabbitMQ)</h3>
              <ol>
                <li>Solicitar credenciales al equipo de Integración (cada módulo tiene su usuario/permiso propio).</li>
                <li>URL del broker: <code>{html.escape(RABBIT_URL)}</code></li>
                <li>Exchange: <code>tolling.bus</code> (tipo: <code>topic</code>, durable).</li>
                <li>Publicar con <code>routing_key</code> del evento correspondiente.</li>
                <li>Consumir declarando una <code>queue</code> durable y binding a la <code>routing_key</code> del evento requerido.</li>
              </ol>
            </div>

            <div class="streams">
              <h3>Publica</h3>
              {publishes if publishes else "<p>No publica eventos.</p>"}
              <h3>Consume</h3>
              {subscribes if subscribes else "<p>No consume eventos.</p>"}
            </div>
          </section>
        """)

    body = "\n".join(mod_cards)

    # HTML completo
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <title>{html.escape(APP_TITLE)}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <style>
    :root {{
      --bg:#0f172a; --panel:#111827; --ink:#e5e7eb; --muted:#94a3b8; --accent:#22d3ee; --chip:#0b2430;
      --ok:#10b981; --warn:#f59e0b; --bad:#ef4444; --code:#0b1220; --border:#1f2937;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin:0; background:var(--bg); color:var(--ink); font-family: ui-sans-serif, system-ui, Segoe UI, Roboto, Helvetica, Arial;
      line-height:1.45;
    }}
    header {{
      position:sticky; top:0; z-index:1; background:linear-gradient(180deg,var(--panel),rgba(17,24,39,.85));
      backdrop-filter: blur(6px); border-bottom:1px solid var(--border);
    }}
    .wrap {{ max-width:1100px; margin:0 auto; padding:18px 20px; }}
    h1 {{ margin:0; font-size:22px; letter-spacing:.3px; }}
    .meta {{ color:var(--muted); font-size:13px; margin-top:6px; }}
    main .wrap {{ padding:22px 20px 64px; }}
    .module {{ border:1px solid var(--border); background:var(--panel); border-radius:16px; padding:18px; margin:18px 0 28px; }}
    h2 {{ margin:0 0 8px; font-size:20px; }}
    h3 {{ margin:20px 0 8px; font-size:16px; color:var(--accent); }}
    .summary {{ color:var(--muted); margin:0 0 8px; }}
    .howto ol {{ margin:8px 0 0 18px; color:var(--muted); }}
    .streams {{ display:grid; gap:18px; grid-template-columns: 1fr; }}
    .event {{ border:1px solid var(--border); border-radius:12px; padding:12px; background:rgba(34,211,238,0.04); }}
    .event .rk {{ background:var(--chip); display:inline-block; padding:4px 8px; border-radius:999px; margin-bottom:8px; }}
    pre {{ background:var(--code); color:var(--ink); border:1px solid var(--border); padding:10px; border-radius:8px; overflow:auto; }}
    code {{ background:rgba(34,211,238,0.08); padding:2px 6px; border-radius:6px; }}
    footer {{ color:var(--muted); font-size:12px; text-align:center; padding:24px; border-top:1px solid var(--border); }}
    @media(min-width:900px) {{ .streams {{ grid-template-columns: 1fr 1fr; }} }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>{html.escape(APP_TITLE)}</h1>
      <div class="meta">Documentación de eventos del bus RabbitMQ, separada por módulo según consignas del TP.</div>
    </div>
  </header>
  <main>
    <div class="wrap">
      {body}
    </div>
  </main>
  <footer>
    Broker: <code>{html.escape(RABBIT_URL)}</code> — Exchange: <code>tolling.bus</code> — Tipo: <code>topic</code>.
  </footer>
</body>
</html>"""

# -----------------------------
# Entry point (gunicorn: app_bus:app)
# -----------------------------
if __name__ == "__main__":
    # Para desarrollo local:
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
