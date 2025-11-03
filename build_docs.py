#!/usr/bin/env python3
import json, os, pathlib, html

CFG = json.load(open("config.json","r",encoding="utf-8"))
HOST  = CFG["rabbit"]["host"]
VHOST = CFG["rabbit"]["vhost"]
EX    = CFG["rabbit"]["exchange"]

def code(s): return "<pre><code>"+html.escape(s)+"</code></pre>"

def conn_line(u,p): return f"amqps://{u}:{p}@{HOST}/{VHOST}"

def publisher_snippet(user, pwd, event_json):
    return f"""# Python + pika
import pika, json
url = "{conn_line(user,pwd)}"
params = pika.URLParameters(url); params.heartbeat=30
conn = pika.BlockingConnection(params); ch = conn.channel()
ch.exchange_declare(exchange="{EX}", exchange_type="topic", durable=True, passive=True)
msg = {json.dumps(event_json, indent=2)}
rk = msg["event"]
ch.basic_publish(exchange="{EX}", routing_key=rk,
                 body=json.dumps(msg).encode(),
                 properties=pika.BasicProperties(content_type="application/json", delivery_mode=2))
conn.close()"""

def consumer_snippet(user,pwd,queue):
    return f"""# Python + pika
import pika
url = "{conn_line(user,pwd)}"
params = pika.URLParameters(url); params.heartbeat=30
conn = pika.BlockingConnection(params); ch = conn.channel()
ch.queue_declare(queue="{queue}", durable=True)
def cb(ch_, m, props, body):
    print(m.routing_key, body.decode()); ch_.basic_ack(m.delivery_tag)
ch.basic_qos(prefetch_count=50)
ch.basic_consume(queue="{queue}", on_message_callback=cb)
ch.start_consuming()"""

CSS = """
body{font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;background:#0b1020;color:#e5e7eb;margin:0}
a{color:#8ab4ff;text-decoration:none} a:hover{text-decoration:underline}
.wrapper{max-width:1100px;margin:0 auto;padding:36px}
h1{margin:0 0 8px 0} h2{margin-top:28px;border-bottom:1px solid #1f2a44;padding-bottom:6px}
.card{background:#0f1629;border:1px solid #1f2a44;border-radius:12px;padding:18px;margin:16px 0}
pre{background:#0b1020;border:1px solid #1f2a44;border-radius:12px;padding:12px;overflow:auto}
.small{font-size:12px;color:#9aa4bd}
.kv{display:grid;grid-template-columns:180px 1fr;gap:8px}
"""

parts = []
parts.append(f"""<!doctype html><html><head><meta charset="utf-8">
<title>Tolling RabbitMQ Bus — Documentación</title>
<style>{CSS}</style></head><body><div class="wrapper">
<h1>Tolling RabbitMQ Bus — Documentación</h1>
<div class="small">RabbitMQ host <code>{HOST}</code> | vhost <code>{VHOST}</code> | exchange <code>{EX}</code></div>
<div class="card">
<b>Reglas</b>
<ul>
<li>Cada módulo tiene usuario propio y cola dedicada.</li>
<li>Publicación limitada por <i>topic-permissions</i> a eventos autorizados.</li>
<li>Lectura limitada a su cola y patrones asociados.</li>
<li>Formato de mensaje: envelope con campos <code>event</code>, <code>version</code>, <code>data</code>, <code>meta</code>.</li>
</ul>
</div>
<h2>Envelope</h2>
{code(json.dumps({"event":"<event-name>","version":"1.0","data":{"...":"..."},"meta":{"occurred_at":"<ISO-8601>","producer":"<module>","correlation_id":"<uuid>"}}, indent=2))}
""")

def section_for(name, m):
    u = m["username"]; p = m["password"]; q = m["queue"]
    events = m["can_publish"]
    bindings = m["bindings"]
    html_s = f'<div class="card"><h3>{name.upper()}</h3>'
    html_s += f'<div class="kv"><div>AMQP URL</div><div><code>{conn_line(u,p)}</code></div></div>'
    html_s += f'<div class="kv"><div>Queue</div><div><code>{q}</code></div></div>'
    html_s += f'<div class="kv"><div>Bindings</div><div>' + ", ".join(f"<code>{b}</code>" for b in bindings) + "</div></div>"
    html_s += f'<div class="kv"><div>Puede publicar</div><div>' + (", ".join(f"<code>{e}</code>" for e in events) if events else "<span class='small'>—</span>") + "</div></div>"
    html_s += "<h4>Consumir</h4>" + code(consumer_snippet(u,p,q))
    if events:
        ev0 = CFG["examples"][events[0]]
        html_s += "<h4>Publicar (ejemplo)</h4>" + code(publisher_snippet(u,p,ev0))
        html_s += "<h5>JSON</h5>" + code(json.dumps(ev0, indent=2))
    html_s += "</div>"
    return html_s

for name, m in CFG["modules"].items():
    parts.append(section_for(name, m))

parts.append("</div></body></html>")
html_out = "\n".join(parts)

pathlib.Path("docs").mkdir(exist_ok=True)
open("docs/index.html","w",encoding="utf-8").write(html_out)
print("docs/index.html generado.")
