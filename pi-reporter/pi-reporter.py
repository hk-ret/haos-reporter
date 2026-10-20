#! /usr/bin/env python3
"""Gather information about a Rspberry or Orange Pi and report using MQTT"""

import os
import os.path
import json
import re
import time
import signal
import socket
from datetime import datetime

import paho.mqtt.client as mqtt
import subprocess
import requests


def detect_environment():
    if os.environ.get("SUPERVISOR_TOKEN"):
        return "haos"
    return "docker"


ENVIRONMENT = detect_environment()
print(f"Starting PI-REPORTER in {ENVIRONMENT} environment")


def get_config():
    "read configuration file"
    with open(
        "/data/options.json" if ENVIRONMENT == "haos" else "options.json"
    ) as cfg_file:
        return json.load(cfg_file)


def on_connect(client, userdata, flags, rc):
    "catch response of connection attempt"
    print(f"PI-REPORTER connected: {rc=}")


def _get_info(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT):
    if isinstance(cmd, list):
        cmd = " ".join(cmd)
    stdout, _ = subprocess.Popen(
        cmd, shell=shell, stdout=stdout, stderr=stderr
    ).communicate()
    return stdout.decode().replace("\000", "")


def get_hostnames():
    "determine hostname of the Pi"
    if ENVIRONMENT == "haos":
        host = socket.gethostname()
        return host, host
    fqdn = _get_info("cat hostname-f.txt").strip()
    host = fqdn.split(".")[0]
    return host, fqdn


def get_model_haos():
    try:
        with open("/device-tree/model", "rb") as f:
            model = f.read().decode(errors="replace").strip("\x00\n ")
    except OSError:
        model = "unknown"

    conn = "?"
    if "Pi 4 Model B" in model or "Pi 3 Model B" in model:
        conn = "e,w,b"
    elif "Pi 5" in model:
        conn = "e,w,b"

    return model, conn


def get_model():
    "determine the model of the Pi"
    if ENVIRONMENT == "haos":
        return get_model_haos()
    model = (
        _get_info("cat proc.device-tree.model.txt")  # /proc/device-tree/model
        # .replace("Raspberry Pi", "RPi")
        .replace("Xunlong", "")
        # .replace("Orange Pi", "OPi")
        .replace("i Model ", "i 1 Model")
        .replace("Rev ", "r")
        .replace(" Plus ", "+")
        .strip()
    )
    # determine available connectivity
    conn = "?"
    if "Pi 4 Model B" in model:
        conn = "e,w,b"
    elif "Pi 3 Model B" in model:
        conn = "e,w,b"
    elif "OPi Zero" in model:
        conn = "e,w"
    elif "OPi 5" in model:
        conn = "e"
    return model, conn


def get_linux_version():
    "determine version of installed Linux"
    lnx = _get_info("grep VERSION_CODENAME /etc/os-release").split("=")[1].strip()
    ver = _get_info("uname -r").strip()
    return lnx, ver


def get_homeassistant_version():
    try:
        response = requests.get(
            "http://supervisor/core/api/config",
            headers={"Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}"},
            timeout=5,
        )
        response.raise_for_status()
        return response.json().get("version", "unknown")
    except Exception as exc:
        print(f"HA version unavailable: {exc}")
        return "unknown"


# def get_ha_sensors(entity_ids):
#     """Read selected Home Assistant entities."""
#     token = os.environ.get("SUPERVISOR_TOKEN")
#     if not token:
#         return {}

#     try:
#         response = requests.get(
#             "http://supervisor/core/api/states",
#             headers={"Authorization": f"Bearer {token}"},
#             timeout=5,
#         )
#         response.raise_for_status()

#         states = {item["entity_id"]: item for item in response.json()}

#         result = {}
#         for entity_id in entity_ids:
#             item = states.get(entity_id)
#             if item and item["state"] not in ("unknown", "unavailable"):
#                 result[entity_id] = {
#                     "state": item["state"],
#                     "attributes": item.get("attributes", {}),
#                 }

#         return result

#     except requests.RequestException as exc:
#         print(f"HA sensor query failed: {exc}")
#         return {}


def get_ha_data():
    """Read Home Assistant System Monitor sensors and HAOS update status."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {}

    entity_ids = {
        "memory_usage_percent": "sensor.system_monitor_arbeitsspeicherauslastung",
        "memory_used": "sensor.system_monitor_belegter_arbeitsspeicher",
        "memory_free": "sensor.system_monitor_freier_arbeitsspeicher",
        "swap_used": "sensor.system_monitor_belegter_swap",
        "swap_free": "sensor.system_monitor_freier_swap",
        #
        "swap_usage_percent": "sensor.system_monitor_swap_auslastung",
        "disk_usage_percent": "sensor.system_monitor_massenspeicher_auslastung",
        "disk_used": "sensor.system_monitor_belegter_massenspeicher",
        "disk_free": "sensor.system_monitor_freier_massenspeicher",
        "network_out": "sensor.system_monitor_netzwerkausgang_enu1u1u1",
        "network_in": "sensor.system_monitor_netzwerkeingang_enu1u1u1",
        "cpu_usage_percent": "sensor.system_monitor_prozessornutzung",
        "cpu_temperature": "sensor.system_monitor_prozessortemperatur",
        "cpu_pressure_10s": "sensor.system_monitor_teilweiser_cpu_engpass_10_s_durchschnitt",
        "cpu_pressure_60s": "sensor.system_monitor_teilweiser_cpu_engpass_60_s_durchschnitt",
        "cpu_pressure_300s": "sensor.system_monitor_teilweiser_cpu_engpass_300_s_durchschnitt",
        "cpu_pressure_total": "sensor.system_monitor_teilweiser_cpu_engpass_gesamt",
        "load_1min": "sensor.system_monitor_last_1_min",
        "load_5min": "sensor.system_monitor_last_5_min",
        "load_15min": "sensor.system_monitor_last_15_min",
        "last_boot": "sensor.system_monitor_betriebszeit",
    }

    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(
            "http://supervisor/core/api/states",
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        states = {item["entity_id"]: item for item in response.json()}
    except (requests.RequestException, ValueError) as exc:
        print(f"HA sensor query failed: {exc}")
        return {}

    result = {}

    for name, entity_id in entity_ids.items():
        item = states.get(entity_id)
        if not item:
            continue

        state = item.get("state")
        if state in ("unknown", "unavailable", None):
            continue

        attrs = item.get("attributes", {})
        result[name] = {
            "state": state,
            "unit": attrs.get("unit_of_measurement"),
        }

    # HAOS update information
    update = states.get("update.home_assistant_operating_system_update")

    if update:
        attrs = update.get("attributes", {})
        result["haos_update"] = {
            "state": update.get("state"),
            "installed_version": attrs.get("installed_version"),
            "latest_version": attrs.get("latest_version"),
            "in_progress": attrs.get("in_progress"),
            "release_url": attrs.get("release_url"),
        }

    return result


def get_drives(root=False):
    "gather info on filesystems"
    fsys = {}
    for info in _get_info(
        [
            "df",
            "--block-size=G",
            "--output=size,pcent,source,target",
            "--exclude-type=tmpfs",
            "--exclude-type=devtmpfs",
        ]
    ).splitlines()[1:]:
        match = re.fullmatch(r"\s*([0-9]+)G\s+([0-9]+)%\s+(\S+)\s+(\S+)", info)
        if match:
            drv = re.sub("/", "-", re.sub(r"^\s*/", "", match.group(4)))
            if not drv:
                if root:
                    return (
                        int(match.group(1)),
                        int(match.group(2)),
                        100 - int(match.group(2)),
                    )
                drv = "root"
            if drv[:6] != "varlib":
                fsys[drv] = {
                    "size_gb": int(match.group(1)),
                    "used_prcnt": int(match.group(2)),
                    "device": match.group(3),
                    "mount_pt": match.group(4),
                }
    # print(f"{match.groups()=} {fsys=}")
    return fsys


def get_cpu_info():
    "get processor info"
    cpu = {}
    cpu["number_cores"] = 0
    # cpu["model"] = ""
    cpu["bogo_mips"] = 0
    # cpu["hardware"] = ""
    # cpu["serial"] = ""
    for item, value in [
        (i[0].lower() if len(i) >= 1 else "", " ".join(i[1:]).strip())
        for i in [
            l.replace("\t", "").split(":")
            for l in _get_info(
                "cat /proc/cpuinfo | /bin/egrep -i 'processor|model|bogomips|hardware|serial'"
            ).splitlines()
        ]
    ]:
        if "processor" in item:
            cpu["number_cores"] += 1
        elif "model" in item:
            cpu["model"] = value
        elif "bogo" in item:
            cpu["bogo_mips"] += float(value)
        elif "hardware" in item:
            cpu["hardware"] = value
        elif "serial" in item:
            cpu["serial"] = value
    cpu_loads_raw = _get_info("cat /proc/loadavg").split()
    cpu["load_1min_prcnt"] = float(cpu_loads_raw[0]) / cpu["number_cores"] * 100
    cpu["load_5min_prcnt"] = float(cpu_loads_raw[1]) / cpu["number_cores"] * 100
    cpu["load_15min_prcnt"] = float(cpu_loads_raw[2]) / cpu["number_cores"] * 100
    # print(f"{cpu=}")
    return cpu


def _get_bytes(text="", unit=None):
    if isinstance(text, list):
        if not unit and len(text) > 1:
            unit = text[1]
        value = int(text[0])
    else:
        try:
            value = int(text)
        except ValueError:
            return _get_bytes(text.split())
    if unit == "kB":
        return value * 1000
    else:
        return value


def apply_ha_memory(ha_data):
    """Replace container memory metrics with HA host sensor values."""
    mem = {}
    mem["size_mb"] = 0
    mem["free_mb"] = 0
    mem["available_mb"] = 0
    mem["size_swap"] = 0
    mem["free_swap"] = 0
    used = ha_data.get("memory_used", {}).get("state")
    free = ha_data.get("memory_free", {}).get("state")
    percent = ha_data.get("memory_usage_percent", {}).get("state")
    swap_used = ha_data.get("swap_used", {}).get("state")
    swap_free = ha_data.get("swap_free", {}).get("state")

    try:
        if used is not None and free is not None:
            used_mib = float(used)
            free_mib = float(free)

            # HA sensors report MiB; existing fields use decimal MB.
            mem["size_mb"] = round((used_mib + free_mib) * 1.048576)
            mem["free_mb"] = round(free_mib * 1.048576)

        if percent is not None:
            mem["mem_used_prcnt"] = round(float(percent))
        if swap_used is not None and swap_free is not None:
            swap_used_mb = float(swap_used)
            swap_free_mb = float(swap_free)
            mem["size_swap"] = round((swap_used_mb + swap_free_mb) * 1.048576)
            mem["free_swap"] = round(swap_free_mb * 1.048576)

    except (TypeError, ValueError):
        print("Could not convert HA memory sensor values")

    return mem


def get_memory_info():
    "get memory usage info"
    mem = {}
    mem["size_mb"] = 0
    mem["free_mb"] = 0
    mem["available_mb"] = 0
    mem["size_swap"] = 0
    mem["free_swap"] = 0
    for item, value in [
        (i[0].lower(), " ".join(i[1:]).strip())
        for i in [
            l.replace("\t", "").split(":")
            for l in _get_info("cat /proc/meminfo").splitlines()
        ]
        if len(i) > 1
    ]:
        if "memtotal" in item:
            mem["size_mb"] = round(_get_bytes(value) / 1000000)
        elif "memfree" in item:
            mem["free_mb"] = round(_get_bytes(value) / 1000000)
        elif "memavail" in item:
            mem["available_mb"] = round(_get_bytes(value) / 1000000)
        elif "swaptotal" in item:
            mem["size_swap"] = round(_get_bytes(value) / 1000000)
        elif "swapfree" in item:
            mem["free_swap"] = round(_get_bytes(value) / 1000000)
    if mem["size_mb"] > 0:
        mem["mem_used_prcnt"] = round(
            (mem["size_mb"] - mem["free_mb"]) / mem["size_mb"] * 100
        )
    else:
        mem["mem_used_prcnt"] = 0

    # print(f"{mem=}")
    return mem


def format_uptime(seconds):
    """Format uptime seconds as a human-readable duration."""
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")

    return " ".join(parts)


def get_uptime():
    "determine system uptime"
    match = re.match(
        # "\s+(\S+)\s+up\s+(.*),(\s[0-9]+\susers?,)\s+load average:(.*)",
        r"\s+(\S+)\s+up\s+(.*?),(\s+([0-9]+)\susers?,)?\s+load average:\s+(.*)",
        _get_info("uptime"),
    )
    if match:
        uptime = match.group(2).strip()
        # now=match.group(1).strip()
        # no_users=int(match.group(4))
    else:
        uptime = "?"
    up_secs = int(_get_info("cut -f1 -d. /proc/uptime"))
    return uptime, up_secs


def get_temp():
    "determine system temperatures"
    try:
        cpu_temp = round(
            float(_get_info("cat /sys/class/thermal/thermal_zone0/temp").strip())
            / 1000,
            1,
        )
    except ValueError:
        cpu_temp = "unknown"
    sys_temp = cpu_temp
    gpu_temp = "TODO (throttled)"
    # try:
    #     match = re.match(
    #         "temp=([0-9.]+).*", _get_info("/usr/bin/vcgencmd measure_temp")
    #     )
    #     if match:
    #         gpu_temp = round(float(match.group(1).strip()), 1)
    #         sys_temp = gpu_temp
    #     else:
    #         gpu_temp = "unknown"
    # except ValueError:
    #     gpu_temp = "unknown"
    # if gpu_temp == "unknown":
    #     try:
    #         match = re.match(
    #             "temp=([0-9.]+).*", _get_info("/opt/vc/bin/vcgencmd measure_temp")
    #         )
    #         if match:
    #             gpu_temp = round(float(match.group(1).strip()), 1)
    #             sys_temp = gpu_temp
    #         else:
    #             gpu_temp = "unknown"
    #     except ValueError:
    #         gpu_temp = "unknown"
    return cpu_temp, gpu_temp, sys_temp


def get_last_apt_update():
    "get time of last update by apt-get"
    # apt-get update writes to following dir (so date changes on update)
    apt_listdir_filespec = "/varlib/apt/lists/partial"
    # apt-get dist-upgrade | autoremove update the following file when actions are taken
    apt_lockdir_filespec = "/varlib/dpkg/lock"
    last_update_dir = _get_info(
        ["/bin/ls -td", apt_listdir_filespec, apt_lockdir_filespec, "2>/dev/null"]
    ).split("\n")[0]
    if last_update_dir:
        return (
            datetime.fromtimestamp(os.path.getmtime(last_update_dir))
            .astimezone()
            .isoformat()
        )
    return "unkown"


def get_networking_details():
    "list networks"
    networks = {}
    current_iface = None

    for line in _get_info(["cat", "ip.a.txt"]).splitlines():
        iface_match = re.match(r"^(\d+):\s+([^:]+):", line)
        if iface_match:
            current_iface = iface_match.group(2)
            networks[current_iface] = {"inet": [], "inet6": []}
        elif current_iface:
            inet_match = re.findall(r"inet\s+(\S+)", line)
            if inet_match:
                networks[current_iface]["inet"].extend(inet_match)

            inet6_match = re.findall(r"inet6\s+(\S+)", line)
            if inet6_match:
                networks[current_iface]["inet6"].extend(inet6_match)

    return networks


# {"info": {"timestamp": "2023-03-08T09:04:42+01:00", "rpi_model": "Xunlong Orange Pi Zero", "ifaces": "e,w,b", "host_name": "aries", "fqdn": "aries", "ux_release": "bullseye\nbullseye-backports\nbullseye-security\nbullseye-updates", "ux_version": "5.15.80-sunxi", "up_time": "19 days,  16:08", "last_update": "2023-01-19T14:34:15+01:00", "fs_total_gb": 32, "fs_free_prcnt": 8, "networking": {"eth0": {"IP": "192.168.1.236", "mac": "02:81:43:f6:a6:a8"}, "wlan0": {"mac": "36:1a:af:5c:0a:8a"}}, "drives": {"dev": {"size_gb": 0, "used_prcnt": 0, "device": "udev", "mount_pt": "/dev"}, "root": {"size_gb": 32, "used_prcnt": 8, "device": "/dev/mmcblk0p1", "mount_pt": "/"}, "var-log": {"size_gb": 0, "used_prcnt": 71, "device": "/dev/zram1", "mount_pt": "/var/log"}}, "memory": {"size_mb": "491.680", "free_mb": "307.895"}, "cpu": {"hardware": "Allwinner sun8i Family", "model": "ARMv7 Processor rev 5 (v7l)", "number_cores": 4, "bogo_mips": "192.00", "serial": "02c0008143f6a6a8", "load_1min_prcnt": 0.0, "load_5min_prcnt": 0.0, "load_15min_prcnt": 0.0}, "throttle": ["Not Available"], "temperature_c": -1.0, "temp_gpu_c": -1.0, "temp_cpu_c": -1.0, "reporter": "ISP-RPi-mqtt-daemon v1.6.0", "report_interval": 5}}
def info_values():
    info = {}
    if ENVIRONMENT == "haos":
        info["reporter"] = "Pi Reporter 1.0.0 (HAOS)"
    else:
        info["reporter"] = "".join(
            [
                config.get("app", "Pi Reporter (Docker)"),
                " v:",
                config.get("version", "?.?.?"),
            ]
        )
    info["timestamp"] = datetime.now().astimezone().isoformat()
    info["report_interval"] = config["report_interval"]
    info["host_name"], info["fqdn"] = get_hostnames()
    info["environment"] = ENVIRONMENT
    info["model"], info["ifaces"] = get_model()
    # info["rpi_model"] = info["model"]
    info["ux_release"], info["ux_version"] = get_linux_version()
    info["drives"] = get_drives()
    info["networking"] = get_networking_details()
    return info


def update_values():
    "gather all information"
    if ENVIRONMENT == "haos":
        ha_data = get_ha_data()
    info = {}
    info["cpu"] = get_cpu_info()
    info["memory"] = (
        apply_ha_memory(ha_data) if ENVIRONMENT == "haos" else get_memory_info()
    )
    info["up_time"], info["up_time_secs"] = get_uptime()
    if info["up_time"] == "?" and info["up_time_secs"] is not None:
        info["up_time"] = format_uptime(info["up_time_secs"])

    info["fs_total_gb"], info["fs_used_prcnt"], info["fs_free_prcnt"] = get_drives(
        root=True
    )
    info["temp_cpu_c"], info["temp_gpu_c"], info["temperature_c"] = get_temp()
    if ENVIRONMENT == "haos":
        info["ha_version"] = get_homeassistant_version()
        info["ha_sensors"] = ha_data
    else:
        info["last_update"] = get_last_apt_update()
    # info["networking"] = get_networking_details()
    info["timestamp"] = datetime.now().astimezone().isoformat()

    return info


def detection_config(host_name, topic):
    uniq_id = host_name + "-monitor"
    config = {}
    config["name"] = host_name + " Monitor"
    config["availability_topic"] = topic + "/status"
    config["device_class"] = "timestamp"
    config["json_attributes_template"] = "{{ value_json.info | tojson }}"
    config["json_attributes_topic"] = topic + "/monitor"
    config["payload_available"] = "online"
    config["payload_not_available"] = "offline"
    config["state_topic"] = topic + "/monitor"
    config["uniq_id"] = uniq_id
    config["value_template"] = "{{ value_json.info.timestamp }}"
    return config


def mqtt_publish(client: mqtt.Client, topic, payload, qos=0, retain=False):
    res = client.publish(topic=topic, payload=payload, qos=qos, retain=retain)
    if res.rc == mqtt.MQTT_ERR_SUCCESS:
        return
    client.reconnect()
    client.publish(topic=topic, payload=payload, qos=qos, retain=retain)


def report_loop(info, mqtt_client, base_topic):
    info.update(update_values())
    # print(f"info = {json.dumps( {'info': info},indent=2)}")
    mqtt_publish(
        mqtt_client, topic=base_topic + "/monitor", payload=json.dumps({"info": info})
    )
    mqtt_publish(mqtt_client, topic=base_topic + "/status", payload="online")
    mqtt_client.loop()


class GracefulKiller:
    kill_now = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, *args):
        self.kill_now = True


config = get_config()
# print(f"{config=}")
mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(username=config["mqtt_user"], password=config["mqtt_pw"])
mqtt_client.on_connect = on_connect
print("Connecting")
mqtt_client.connect(host=config["mqtt_host"])

info = info_values()
base_topic = config["info_topic"] + info["host_name"]
detection_config = detection_config(host_name=info["host_name"], topic=base_topic)
# print(f"detection_config = {json.dumps( detection_config,indent=2)}")
mqtt_publish(
    mqtt_client,
    topic=config["discovery_topic"].replace("@", info["host_name"]),
    payload=json.dumps(detection_config),
    retain=True,
)
try:
    killer = GracefulKiller()
    while not killer.kill_now:
        report_loop(info=info, mqtt_client=mqtt_client, base_topic=base_topic)
        sleep_step = 2
        sleep_time = info["report_interval"] * 60
        while (sleep_time > 0) and not killer.kill_now:
            time.sleep(sleep_step)
            sleep_time -= sleep_step
            # mqtt_publish(mqtt_client,
            #     topic="pi-monitor/sensor/hydra" + "/status", payload="online"
            # )
            mqtt_client.loop()
except KeyboardInterrupt:
    print("Interrupted from Keyboard")

mqtt_publish(mqtt_client, topic=base_topic + "/status", payload="offline")
mqtt_client.loop()

print("Disconnecting")
mqtt_client.disconnect()
