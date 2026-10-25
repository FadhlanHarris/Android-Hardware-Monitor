import csv
import json
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, send_file

app = Flask(__name__)
ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
CSV_FILE = LOG_DIR / "android_telemetry.csv"
LOCK = threading.Lock()
_prev_cpu = {}

FIELDS = [
    "timestamp", "serial", "manufacturer", "brand", "model", "device", "android",
    "sdk", "soc", "abi", "cpu_cores", "cpu_percent", "ram_used_bytes",
    "ram_total_bytes", "ram_available_bytes", "ram_percent", "storage_used_bytes",
    "storage_total_bytes", "storage_free_bytes", "storage_percent", "battery_percent",
    "battery_status", "battery_temp_c", "battery_voltage_mv", "gpu_name", "gpu_load_percent",
    "cpu_temp_c", "gpu_temp_c", "surface_temp_c", "fps", "thermal_json"
]

def cmd(args, timeout=5):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.stdout.strip() if p.returncode == 0 else ""
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""

def adb_devices():
    raw = cmd(["adb", "devices", "-l"])
    rows = []
    for line in raw.splitlines()[1:]:
        p = line.split()
        if len(p) >= 2 and p[1] == "device":
            rows.append({"serial": p[0], "details": " ".join(p[2:])})
    return rows

def sh(serial, command, timeout=5):
    return cmd(["adb", "-s", serial, "shell", command], timeout=timeout)

def prop(serial, key):
    return sh(serial, f"getprop {key}")

def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def get_device(serial):
    brand = prop(serial, "ro.product.brand") or "Unknown"
    manufacturer = prop(serial, "ro.product.manufacturer") or brand
    model = prop(serial, "ro.product.model") or "Unknown Android device"
    device = prop(serial, "ro.product.device") or "Unknown"
    android = prop(serial, "ro.build.version.release") or "Unknown"
    sdk = prop(serial, "ro.build.version.sdk") or "Unknown"
    abi = prop(serial, "ro.product.cpu.abi") or "Unknown"
    soc = (prop(serial, "ro.soc.model") or prop(serial, "ro.vendor.soc.model") or
           prop(serial, "ro.board.platform") or prop(serial, "ro.hardware") or
           "Not exposed by vendor firmware")
    cores = None
    try:
        cores = int(sh(serial, "getconf _NPROCESSORS_ONLN"))
    except ValueError:
        pass
    return {"serial": serial, "brand": brand, "manufacturer": manufacturer,
            "model": model, "device": device, "android": android, "sdk": sdk,
            "abi": abi, "soc": soc, "cores": cores}

def get_cpu(serial):
    raw = sh(serial, "cat /proc/stat")
    line = next((x for x in raw.splitlines() if x.startswith("cpu ")), "")
    p = line.split()
    if len(p) < 5:
        return None
    try:
        vals = [int(x) for x in p[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        old = _prev_cpu.get(serial)
        _prev_cpu[serial] = (total, idle)
        if old:
            dt, di = total-old[0], idle-old[1]
            if dt > 0:
                return round(max(0, min(100, (dt-di)/dt*100)), 1)
    except ValueError:
        pass
    return None

def get_ram(serial):
    raw = sh(serial, "cat /proc/meminfo")
    values = {}
    for line in raw.splitlines():
        m = re.match(r"([^:]+):\s+(\d+)", line)
        if m:
            values[m.group(1)] = int(m.group(2)) * 1024
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    if total and available is not None:
        used = max(0, total-available)
        return {"total_bytes": total, "used_bytes": used, "available_bytes": available,
                "percent": round(used/total*100, 1)}
    return {"total_bytes": None, "used_bytes": None, "available_bytes": None, "percent": None}

def get_storage(serial):
    raw = sh(serial, "df -k /data")
    for line in reversed(raw.splitlines()[1:]):
        p = line.split()
        if len(p) >= 5 and "/data" in p[-1]:
            try:
                total, used, free = map(int, p[1:4])
                m = re.search(r"(\d+)%", line)
                return {"total_bytes": total*1024, "used_bytes": used*1024,
                        "free_bytes": free*1024,
                        "percent": float(m.group(1)) if m else round(used/total*100, 1)}
            except (ValueError, ZeroDivisionError):
                pass
    return {"total_bytes": None, "used_bytes": None, "free_bytes": None, "percent": None}

def get_battery(serial):
    raw = sh(serial, "dumpsys battery")
    out = {"level": None, "temperature_c": None, "voltage_mv": None, "status": "Unknown"}
    statuses = {"1":"Unknown", "2":"Charging", "3":"Discharging", "4":"Not charging", "5":"Full"}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        if k == "level":
            out["level"] = num(v)
        elif k == "temperature":
            val = num(v)
            out["temperature_c"] = round(val/10, 1) if val is not None else None
        elif k == "voltage":
            out["voltage_mv"] = num(v)
        elif k == "status":
            out["status"] = statuses.get(v, v)
    return out


def get_thermal(serial):
    raw = sh(serial, "dumpsys thermalservice", timeout=10)
    sensors = {}

    # Prioritaskan data suhu terkini dari Thermal HAL.
    current = raw.split("Current temperatures from HAL:", 1)
    if len(current) > 1:
        current_raw = current[1].split(
            "Current cooling devices from HAL:", 1
        )[0]
    else:
        current_raw = raw

    # Baca format Android:
    # Temperature{mValue=41.805, mType=0, mName=CPU, mStatus=0}
    pattern = (
        r"Temperature\{\s*mValue=(-?\d+(?:\.\d+)?),"
        r"\s*mType=(\d+),\s*mName=([^,}]+)"
    )

    for value, sensor_type, name in re.findall(
        pattern, current_raw, re.IGNORECASE
    ):
        temp = num(value)
        name = name.strip().upper()

        if temp is not None and -20 <= temp <= 150:
            sensors[name] = round(temp, 1)

    # Fallback jika format Thermal HAL tidak ditemukan.
    if not sensors:
        for line in raw.splitlines():
            m = re.search(
                r"\b(CPU|GPU|BATTERY|SKIN|SOC|AP|NPU|TPU)\b"
                r".*?(-?\d+(?:\.\d+)?)\s*(?:°?C)\b",
                line,
                re.IGNORECASE,
            )
            if m:
                temp = num(m.group(2))
                if temp is not None and -20 <= temp <= 150:
                    sensors[m.group(1).upper()] = round(temp, 1)

    return sensors

def get_gpu(serial):
    values = [prop(serial, "ro.hardware.egl"), prop(serial, "debug.egl.renderer"),
              prop(serial, "ro.hardware.vulkan")]
    name = next((v for v in values if v and v.lower() not in ("unknown", "0")), None)
    if not name:
        raw = sh(serial, "dumpsys SurfaceFlinger")
        m = re.search(r"(?:GLES|GL_RENDERER|Renderer)\s*[:=]\s*([^\r\n]+)", raw, re.I)
        name = m.group(1).strip()[:120] if m else None
    return {"name": name or "Not exposed by this device", "load_percent": None}

def get_fps(serial):
    # Best effort only; OEM/Android versions may restrict SurfaceFlinger frame stats.
    raw = sh(serial, "dumpsys SurfaceFlinger --timestats")
    vals = []
    for line in raw.splitlines():
        m = re.search(r"(?:totalFrames|presentedFrames)\s*[:=]\s*(\d+)", line, re.I)
        if m:
            vals.append(int(m.group(1)))
    # Counter-only output cannot yield a valid FPS without interval deltas; do not fabricate.
    return {"fps": None, "source": "N/A — FPS real-time perlu sumber frame stats yang didukung"}

def build(serial):
    d = get_device(serial)
    ram, storage, battery = get_ram(serial), get_storage(serial), get_battery(serial)
    thermal, gpu = get_thermal(serial), get_gpu(serial)
    cpu = get_cpu(serial)
    cpu_t = next((v for k,v in thermal.items() if any(t in k for t in ("CPU","SOC","AP"))), None)
    gpu_t = next((v for k,v in thermal.items() if "GPU" in k), None)
    surface_t = next((v for k,v in thermal.items() if any(t in k for t in ("SKIN","SURFACE"))), None)
    return {"connected": True, "device": d, "cpu_percent": cpu, "ram": ram,
            "storage": storage, "battery": battery, "thermal": thermal, "gpu": gpu,
            "fps": get_fps(serial), "temperatures": {
                "cpu": cpu_t, "gpu": gpu_t, "battery": battery["temperature_c"],
                "surface": surface_t}, "timestamp": datetime.now().strftime("%H:%M:%S")}

def log_csv(data):
    d, ram, storage, bat = data["device"], data["ram"], data["storage"], data["battery"]
    t = data["temperatures"]
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"), "serial": d["serial"],
        "manufacturer": d["manufacturer"], "brand": d["brand"], "model": d["model"],
        "device": d["device"], "android": d["android"], "sdk": d["sdk"], "soc": d["soc"],
        "abi": d["abi"], "cpu_cores": d["cores"], "cpu_percent": data["cpu_percent"],
        "ram_used_bytes": ram["used_bytes"], "ram_total_bytes": ram["total_bytes"],
        "ram_available_bytes": ram["available_bytes"], "ram_percent": ram["percent"],
        "storage_used_bytes": storage["used_bytes"], "storage_total_bytes": storage["total_bytes"],
        "storage_free_bytes": storage["free_bytes"], "storage_percent": storage["percent"],
        "battery_percent": bat["level"], "battery_status": bat["status"],
        "battery_temp_c": bat["temperature_c"], "battery_voltage_mv": bat["voltage_mv"],
        "gpu_name": data["gpu"]["name"], "gpu_load_percent": data["gpu"]["load_percent"],
        "cpu_temp_c": t["cpu"], "gpu_temp_c": t["gpu"], "surface_temp_c": t["surface"],
        "fps": data["fps"]["fps"], "thermal_json": json.dumps(data["thermal"], ensure_ascii=False)
    }
    with LOCK:
        new = not CSV_FILE.exists() or CSV_FILE.stat().st_size == 0
        with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/devices")
def devices():
    if not shutil.which("adb"):
        return jsonify({"ok": False, "message": "ADB tidak ditemukan di PATH", "devices": []})
    return jsonify({"ok": True, "devices": adb_devices()})

@app.route("/api/status")
def status():
    if not shutil.which("adb"):
        return jsonify({"connected": False, "message": "ADB tidak ditemukan. Instal Platform-Tools dan atur PATH."})
    connected = adb_devices()
    if not connected:
        return jsonify({"connected": False, "message": "Tidak ada perangkat ADB berstatus device. Periksa kabel, USB debugging, dan izin pada HP."})
    serial = connected[0]["serial"]
    try:
        data = build(serial)
        data["connected_devices"] = connected
        log_csv(data)
        return jsonify(data)
    except Exception as e:
        app.logger.exception("Telemetry error")
        return jsonify({"connected": False, "message": "Gagal membaca perangkat: " + str(e)})

@app.route("/download-csv")
def download_csv():
    if not CSV_FILE.exists():
        with LOCK, CSV_FILE.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()
    return send_file(CSV_FILE, as_attachment=True, download_name="android_telemetry.csv", mimetype="text/csv")

if __name__ == "__main__":
    if not shutil.which("adb"):
        print("PERINGATAN: ADB tidak ditemukan di PATH.")
    print("Android Hardware Monitor Universal — http://127.0.0.1:5000")
    print("CSV: " + str(CSV_FILE))
    app.run(host="127.0.0.1", port=5000, debug=False)
