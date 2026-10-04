# -*- coding: utf-8 -*-
"""Gemini Video Studio - Flask web app (mobile-friendly)."""
import json
import os
import threading
import time
import uuid

from flask import Flask, request, jsonify, render_template, send_file, redirect

import pipeline

BASE = os.path.dirname(os.path.abspath(__file__))
JOBS = os.path.join(BASE, "jobs")
CONFIG = os.path.join(BASE, "config.json")
os.makedirs(JOBS, exist_ok=True)

app = Flask(__name__)

jobs = {}  # job_id -> {status, percent, stage, error, result}


def load_config():
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(cfg):
    with open(CONFIG, "w") as f:
        json.dump(cfg, f)
    os.chmod(CONFIG, 0o600)


def get_api_key():
    return os.environ.get("GEMINI_API_KEY") or load_config().get("api_key")


@app.route("/")
def index():
    return render_template("index.html",
                           has_key=bool(get_api_key()))


@app.route("/save_key", methods=["POST"])
def save_key():
    key = (request.form.get("api_key") or "").strip()
    if not key:
        return redirect("/?err=key")
    cfg = load_config()
    cfg["api_key"] = key
    save_config(cfg)
    return redirect("/?ok=key")


@app.route("/api/start", methods=["POST"])
def start():
    data = request.get_json(force=True)
    script = (data.get("script") or "").strip()
    if len(script) < 20:
        return jsonify({"ok": False, "error": "Script bahut chhota hai (kam se kam 2-3 line likho)"}), 400
    demo = bool(data.get("demo"))
    api_key = get_api_key()
    if not demo and not api_key:
        return jsonify({"ok": False,
                        "error": "Pehle Gemini API key save karo (neeche Settings me)"}), 400
    job_id = uuid.uuid4().hex[:10]
    job_dir = os.path.join(JOBS, job_id)
    # Ek waqt me sirf ek video — free server ki memory (512MB) do sath
    # chal rahe ffmpeg encode se full ho kar crash ho jaati hai.
    if any(j.get("status") == "running" for j in jobs.values()):
        return jsonify({"ok": False, "error": "Ek video abhi ban rahi hai, 2-3 minute ruk kar dobara try karo"}), 429
    cfg = {
        "script": script,
        "demo": demo,
        "api_key": None if demo else api_key,
        "voice": data.get("voice", "male"),
        "quality": data.get("quality", "fast"),
        "format": data.get("format", "vertical"),
        "style": data.get("style", "cinematic"),
        "kenburns": bool(data.get("kenburns", True)),
        "captions": bool(data.get("captions", True)),
        "speed": int(data.get("speed", 100)),
    }
    jobs[job_id] = {"status": "running", "percent": 0,
                    "stage": "Shuru ho raha hai...", "error": None,
                    "result": None}

    def progress(pct, stage):
        jobs[job_id]["percent"] = pct
        jobs[job_id]["stage"] = stage

    def worker():
        try:
            final = pipeline.run_job(job_dir, cfg, progress)
            jobs[job_id].update({"status": "done", "percent": 100,
                                 "stage": "Video ready!",
                                 "result": f"/download/{job_id}"})
        except Exception as e:  # noqa: BLE001
            jobs[job_id].update({"status": "error",
                                 "error": str(e)[:600]})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/status/<job_id>")
def status(job_id):
    j = jobs.get(job_id)
    if not j:
        return jsonify({"ok": False, "error": "Job nahi mila"}), 404
    return jsonify({"ok": True, **j})


@app.route("/download/<job_id>")
def download(job_id):
    path = os.path.join(JOBS, job_id, "final_video.mp4")
    if not os.path.exists(path):
        return "Video nahi mili", 404
    return send_file(path, as_attachment=True,
                     download_name="gemini_video.mp4",
                     mimetype="video/mp4")


@app.route("/health")
def health():
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
