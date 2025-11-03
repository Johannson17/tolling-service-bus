#!/usr/bin/env python3
from flask import Flask, send_from_directory
import os

PORT = int(os.environ.get("PORT", "9090"))
app = Flask(__name__, static_folder="docs")

@app.get("/")
def root():
    return send_from_directory("docs", "index.html")

@app.get("/<path:asset>")
def assets(asset):
    return send_from_directory("docs", asset)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
