from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json
import time

DATA = Path(__file__).resolve().parent / "data"
STATE_FILE = DATA / "relay_state.json"
PORT = 8898


def now_ms():
    return int(time.time() * 1000)


def load_state():
    DATA.mkdir(exist_ok=True)
    if not STATE_FILE.exists():
        return {"stations": {}, "logs": []}
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        state.setdefault("stations", {})
        state.setdefault("logs", [])
        return state
    except Exception:
        return {"stations": {}, "logs": []}


def save_state(state):
    DATA.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def text(value, limit=200):
    return str(value or "").replace("<", "").replace(">", "")[:limit]


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    return json.loads(raw)


def auth(state, station_id, station_key):
    station = state["stations"].get(station_id)
    if station and station.get("stationKey") == station_key:
        return station
    return None


def target_matches(device, target):
    t = (target or {}).get("type")
    value = str((target or {}).get("value") or "")
    return t == "all" or (t == "group" and device.get("group") == value) or (t == "deviceId" and device.get("deviceId") == value)


def upsert_device(station, device):
    devices = station.setdefault("devices", [])
    device_id = text(device.get("deviceId") or device.get("deviceLocalId"), 120)
    if not device_id:
        return None, "missing_device"
    fixed = dict(device)
    fixed["deviceId"] = device_id
    fixed["lastSeen"] = now_ms()
    for idx, old in enumerate(devices):
        if old.get("deviceId") == device_id:
            merged = {**old, **fixed}
            if isinstance(old.get("appMap"), dict) and isinstance(fixed.get("appMap"), dict):
                merged["appMap"] = {**old.get("appMap", {}), **fixed.get("appMap", {})}
            devices[idx] = merged
            return merged, "updated"
    devices.append(fixed)
    return fixed, "added"


def jobs_for_device(station, device_id):
    device = next((d for d in station.get("devices", []) if d.get("deviceId") == device_id), None)
    if not device:
        return []
    all_jobs = []
    for mission in active_missions(station):
        all_jobs.extend(jobs_for_mission_device(mission, device, device_id))
    return all_jobs


def active_missions(station):
    missions = []
    seen = set()
    for mission in station.get("activeMissions", []):
        if not isinstance(mission, dict) or mission.get("cancelled"):
            continue
        mission_id = mission.get("missionId") or id(mission)
        if mission_id in seen:
            continue
        seen.add(mission_id)
        missions.append(mission)
    legacy = station.get("mission")
    if isinstance(legacy, dict) and not legacy.get("cancelled"):
        mission_id = legacy.get("missionId") or id(legacy)
        if mission_id not in seen:
            missions.append(legacy)
    return missions


def jobs_for_mission_device(mission, device, device_id):
    jobs = []
    if isinstance(mission.get("jobs"), list):
        for job in mission.get("jobs", []):
            if target_matches(device, job.get("target")):
                jobs.append({
                    "missionId": mission.get("missionId"),
                    "missionName": mission.get("missionName"),
                    "jobId": job.get("jobId"),
                    "jobName": job.get("jobName"),
                    "steps": job.get("steps", []),
                    "appMap": device.get("appMap", {})
                })
        return jobs
    if target_matches(device, mission.get("targets")):
        jobs.append({
            "missionId": mission.get("missionId"),
            "missionName": mission.get("missionName"),
            "jobId": mission.get("jobId"),
            "jobName": mission.get("missionName"),
            "steps": mission.get("steps", []),
            "appMap": device.get("appMap", {})
        })
    return jobs


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Station-Id, X-Station-Key")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        state = load_state()
        if parsed.path == "/api/relay/health":
            self.send_json({"ok": True, "stationCount": len(state["stations"])})
            return
        if parsed.path == "/api/relay/mission":
            qs = parse_qs(parsed.query)
            station_id = text((qs.get("stationId") or [""])[0], 120)
            station_key = text((qs.get("stationKey") or [""])[0], 160)
            device_id = text((qs.get("deviceId") or [""])[0], 120)
            station = auth(state, station_id, station_key)
            if not station:
                self.send_json({"ok": False, "error": "bad_station"}, 403)
                return
            jobs = jobs_for_device(station, device_id)
            self.send_json({"ok": True, "jobs": jobs, "missionId": (station.get("mission") or {}).get("missionId")})
            return
        if parsed.path == "/api/relay/state":
            qs = parse_qs(parsed.query)
            station_id = text((qs.get("stationId") or [""])[0], 120)
            station_key = text((qs.get("stationKey") or [""])[0], 160)
            station = auth(state, station_id, station_key)
            if not station:
                self.send_json({"ok": False, "error": "bad_station"}, 403)
                return
            self.send_json({
                "ok": True,
                "station": station.get("station"),
                "devices": station.get("devices", []),
                "jobStatus": station.get("jobStatus", {}),
                "updatedAt": station.get("updatedAt", 0)
            })
            return
        self.send_json({"ok": False, "error": "not_found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        state = load_state()
        try:
            payload = read_json(self)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"bad_json: {exc}"}, 400)
            return

        if parsed.path == "/api/relay/register_station":
            station_id = text(payload.get("stationId"), 120)
            station_key = text(payload.get("stationKey"), 160)
            if not station_id or not station_key:
                self.send_json({"ok": False, "error": "missing_station"}, 400)
                return
            station = state["stations"].setdefault(station_id, {})
            station["stationId"] = station_id
            station["stationKey"] = station_key
            station.setdefault("devices", [])
            station.setdefault("mission", None)
            station.setdefault("activeMissions", [])
            station.setdefault("jobStatus", {})
            station["updatedAt"] = now_ms()
            save_state(state)
            self.send_json({"ok": True})
            return

        station_id = text(self.headers.get("X-Station-Id"), 120) or text(payload.get("stationId"), 120)
        station_key = text(self.headers.get("X-Station-Key"), 160) or text(payload.get("stationKey"), 160)
        station = auth(state, station_id, station_key)
        if not station:
            self.send_json({"ok": False, "error": "bad_station"}, 403)
            return

        if parsed.path == "/api/relay/push_state":
            station["station"] = payload.get("station")
            if isinstance(payload.get("devices"), list):
                for device in payload.get("devices", []):
                    if isinstance(device, dict):
                        upsert_device(station, device)
            station["mission"] = payload.get("mission")
            station["activeMissions"] = payload.get("activeMissions") if isinstance(payload.get("activeMissions"), list) else ([payload.get("mission")] if payload.get("mission") else [])
            station["updatedAt"] = now_ms()
            save_state(state)
            self.send_json({"ok": True, "deviceCount": len(station.get("devices", []))})
            return

        if parsed.path == "/api/relay/report/device":
            device, result = upsert_device(station, payload)
            station["updatedAt"] = now_ms()
            save_state(state)
            self.send_json({"ok": bool(device), "result": result, "deviceId": device.get("deviceId") if device else ""}, 200 if device else 400)
            return

        if parsed.path == "/api/relay/report/apps":
            device_id = text(payload.get("deviceId") or payload.get("deviceLocalId"), 120)
            device = next((d for d in station.get("devices", []) if d.get("deviceId") == device_id), None)
            if not device:
                device, _ = upsert_device(station, {"deviceId": device_id, "label": device_id, "group": "cum_a"})
            if isinstance(payload.get("apps"), list):
                device["apps"] = payload.get("apps")
            if isinstance(payload.get("appMap"), dict):
                device["appMap"] = {**device.get("appMap", {}), **payload.get("appMap", {})}
            device["lastSeen"] = now_ms()
            station["updatedAt"] = now_ms()
            save_state(state)
            self.send_json({"ok": True, "deviceId": device_id})
            return

        if parsed.path == "/api/relay/job/status":
            device_id = text(payload.get("deviceId"), 120)
            job_id = text(payload.get("jobId"), 160)
            station.setdefault("jobStatus", {})[f"{device_id}:{job_id}"] = {
                "deviceId": device_id,
                "jobId": job_id,
                "status": text(payload.get("status"), 80),
                "message": text(payload.get("message"), 500),
                "updatedAt": now_ms()
            }
            save_state(state)
            self.send_json({"ok": True})
            return

        self.send_json({"ok": False, "error": "not_found"}, 404)


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    print(f"Chủ Nhà V4.0.4 Relay: http://0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
