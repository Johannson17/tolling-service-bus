#!/usr/bin/env python3
import os, pika, sys
from config import config   # ver config.py

team = os.environ.get("TEAM", "module3")
url  = os.environ.get("AMQP_URL", config(team))
queue = {
  "module2":"module2.rates.q",
  "module3":"module3.ops.q",
  "module4":"module4.payments.q",
  "module5":"module5.fines.q",
  "module6":"module6.external.q",
  "module7":"module7.dashboard.q",
  "audit":"audit.q"
}[team]

params = pika.URLParameters(url); params.heartbeat=30
conn = pika.BlockingConnection(params); ch = conn.channel()
ch.queue_declare(queue=queue, durable=True)

def cb(ch_, m, props, body):
    print(m.routing_key, body.decode())
    ch_.basic_ack(m.delivery_tag)

ch.basic_qos(prefetch_count=50)
ch.basic_consume(queue=queue, on_message_callback=cb)
print(f"consuming {queue} as {team}…")
try:
    ch.start_consuming()
except KeyboardInterrupt:
    conn.close()
