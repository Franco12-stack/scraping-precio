import dataclasses
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

import config
from report import guardar_csv, guardar_html
from scrapers import CompraGamerScraper, HardGamerScraper, MercadoLibreScraper, MexxScraper
from scrapers.base import Resultado

app = Flask(__name__)

SCRAPERS = [
    MercadoLibreScraper(),
    CompraGamerScraper(),
    HardGamerScraper(),
    MexxScraper(),
]

# job_id -> {results, status, queue, timestamp, productos}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/buscar", methods=["POST"])
def buscar():
    data = request.get_json(silent=True) or {}
    raw = data.get("productos", "")
    productos = [p.strip() for p in raw.splitlines() if p.strip()]
    max_resultados = max(1, min(int(data.get("max_resultados", config.MAX_RESULTS)), 10))

    if not productos:
        return jsonify({"error": "Ingresá al menos un producto."}), 400

    import queue as _queue
    job_id = str(uuid.uuid4())
    q = _queue.Queue()
    with _jobs_lock:
        _jobs[job_id] = {
            "queue": q,
            "results": {},
            "status": "running",
            "productos": productos,
            "timestamp": datetime.now().isoformat(),
        }

    threading.Thread(
        target=_ejecutar_scrapers,
        args=(job_id, productos, max_resultados),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id})


@app.route("/stream/<job_id>")
def stream(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return Response("Job no encontrado", status=404)

    def generate():
        q = job["queue"]
        while True:
            try:
                msg = q.get(timeout=90)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg.get("type") == "done":
                    break
            except Exception:
                yield 'data: {"type":"ping"}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/descargar/<job_id>/<formato>")
def descargar(job_id, formato):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return "Job no encontrado", 404
    if job["status"] != "done":
        return "Búsqueda en curso", 409

    resultados_obj: dict[str, list[Resultado]] = {
        prod: [Resultado(**r) for r in items]
        for prod, items in job["results"].items()
    }
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    if formato == "csv":
        ruta = guardar_csv(resultados_obj, config.OUTPUT_DIR)
        return send_file(ruta, as_attachment=True, download_name="precios.csv")
    if formato == "html":
        ruta = guardar_html(resultados_obj, config.OUTPUT_DIR)
        return send_file(ruta, as_attachment=True, download_name="precios.html")
    return "Formato no soportado", 400


def _ejecutar_scrapers(job_id: str, productos: list[str], max_resultados: int):
    with _jobs_lock:
        job = _jobs[job_id]
    q = job["queue"]

    try:
        for producto in productos:
            q.put({"type": "producto_inicio", "producto": producto})
            job["results"][producto] = []

            with ThreadPoolExecutor(max_workers=len(SCRAPERS)) as executor:
                futuros = {
                    executor.submit(sc.buscar, producto, max_resultados): sc.site_name
                    for sc in SCRAPERS
                }
                for futuro in as_completed(futuros):
                    sitio = futuros[futuro]
                    try:
                        resultados = futuro.result()
                        serializados = [dataclasses.asdict(r) for r in resultados]
                        job["results"][producto].extend(serializados)
                        q.put({
                            "type": "sitio_ok",
                            "producto": producto,
                            "sitio": sitio,
                            "resultados": serializados,
                        })
                    except Exception as exc:
                        q.put({
                            "type": "sitio_error",
                            "producto": producto,
                            "sitio": sitio,
                            "error": str(exc),
                        })

            q.put({"type": "producto_listo", "producto": producto})

    except Exception as exc:
        q.put({"type": "error_fatal", "error": str(exc)})
    finally:
        job["status"] = "done"
        q.put({"type": "done", "job_id": job_id})


if __name__ == "__main__":
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
