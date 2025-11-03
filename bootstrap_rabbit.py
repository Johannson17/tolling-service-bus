#!/usr/bin/env python3
import json, os, sys, base64, time
import requests

CFG = json.load(open("config.json", "r", encoding="utf-8"))

HOST   = CFG["rabbit"]["host"]
VHOST  = CFG["rabbit"]["vhost"]
ADMIN  = (CFG["rabbit"]["admin_user"], CFG["rabbit"]["admin_pass"])
API    = f"https://{HOST}/api"

EXCHANGE = CFG["rabbit"]["exchange"]
EX_TYPE  = CFG["rabbit"]["exchange_type"]
DLX      = CFG["rabbit"]["dlx"]
TTL_MS   = int(CFG["rabbit"]["ttl_ms"])

def rq(method, path, data=None, ok=(200,201,202,204)):
    url = f"{API}{path}"
    r = requests.request(method, url, auth=ADMIN,
                         headers={"content-type":"application/json"},
                         data=(json.dumps(data) if data is not None else None))
    if r.status_code not in ok:
        print(f"[ERR] {method} {path} {r.status_code} {r.text}")
        sys.exit(1)
    return r

def upsert_vhost():
    rq("PUT", f"/vhosts/{VHOST}")
    # enable tracing off (idempotente)
    rq("PUT", f"/vhosts/{VHOST}/trace", {"tracing": False})

def upsert_exchanges():
    rq("PUT", f"/exchanges/{VHOST}/{DLX}", {"type":"fanout","durable":True})
    rq("PUT", f"/exchanges/{VHOST}/{EXCHANGE}", {"type":EX_TYPE,"durable":True})

def upsert_queue(qname):
    args = {"x-dead-letter-exchange": DLX, "x-message-ttl": TTL_MS}
    rq("PUT", f"/queues/{VHOST}/{qname}", {"durable":True,"arguments":args})

def bind(qname, routing_key):
    rq("POST", f"/bindings/{VHOST}/e/{EXCHANGE}/q/{qname}", {"routing_key": routing_key})

def upsert_user(u, p):
    rq("PUT", f"/users/{u}", {"password": p, "tags": ""})

def set_permissions(u, configure_re, write_re, read_re):
    rq("PUT", f"/permissions/{VHOST}/{u}",
       {"configure": configure_re, "write": write_re, "read": read_re})

def set_topic_permissions(u, write_re, read_re="^.*$"):
    rq("PUT", f"/topic-permissions/{VHOST}/{u}",
       {"exchange": EXCHANGE, "write": write_re, "read": read_re})

def main():
    print("[*] Applying topology and security to CloudAMQP…")
    upsert_vhost()
    upsert_exchanges()

    # queues + bindings
    for name, m in CFG["modules"].items():
        q = m["queue"]
        upsert_queue(q)
        for rk in m["bindings"]:
            bind(q, rk)

        # DLQ queue (optional but recomendado)
        dlq = f"{q}.dlq"
        rq("PUT", f"/queues/{VHOST}/{dlq}", {"durable": True})
        rq("POST", f"/bindings/{VHOST}/e/{DLX}/q/{dlq}", {"routing_key": ""})

    # users + permissions
    for name, m in CFG["modules"].items():
        u, p = m["username"], m["password"]
        upsert_user(u, p)

        # Restricciones: su cola, escribir solo en exchange, leer solo su cola
        q = m["queue"]
        set_permissions(
            u,
            configure_re=f"^{q}$",
            write_re=f"^{EXCHANGE}$",
            read_re=f"^{q}$"
        )
        # Topic-permissions: qué eventos puede publicar
        if m["can_publish"]:
            write_pat = "^(" + "|".join(map(lambda s: s.replace(".","\\."),
                                            m["can_publish"])) + ")$"
        else:
            write_pat = "$^"  # nada
        # Lectura: cinturón (permitir lo que ya bindemos)
        if m["bindings"] == ["#"]:
            read_pat = "^.*$"
        else:
            read_pat = "^(" + "|".join(map(lambda s: s.replace(".","\\.").replace("\\*",".*"),
                                           m["bindings"])) + ")$"
        set_topic_permissions(u, write_re=write_pat, read_re=read_pat)

    print("[OK] Topología y permisos aplicados.")
    print(f"    vhost: {VHOST}")
    print(f"    exchange: {EXCHANGE} ({EX_TYPE})  dlx: {DLX}")
    print("    users:", ", ".join(m['username'] for m in CFG['modules'].values()))

if __name__ == "__main__":
    main()
