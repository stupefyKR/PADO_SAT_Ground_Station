"""Synthetic equipment parameters for the supplied PDR housekeeping groups.

Values and JSON field shapes are simulator data, never vendor wire layouts.
The vendor log byte counts are retained as references, not fabricated records.
"""

from __future__ import annotations

import json
import math
import zlib

from tools.pado_packet_codec import decode, encode_tm, load_catalog


FULL_HK_PERIOD_S = 600
MAX_GENERATED_PDUS = 256
SIM_MODES = {"Initial": 1, "Standby": 2, "Communication": 3,
             "Mission(CREST)": 4, "Mission(SIREN)": 5, "Safehold": 6}
SCALAR_FAMILIES = ("EPS_PDU1", "AOCS_STATE", "GNSS_PVT", "EPS_P60", "EPS_PDU2",
                   "EPS_ACU", "EPS_BPX", "EPS_POWER", "OBC_HEALTH", "AX100_LINK", "SIREN_ALERT",
                   "FDIR_EVENT", "CREST_HEALTH", "SIREN_HEALTH")


def _wave(stamp_s: int, center: float, amplitude: float, period_s: float,
          phase: float = 0.0) -> float:
    return center + amplitude * math.sin(stamp_s * 2 * math.pi / period_s + phase)


def _watchdog() -> dict:
    return {"wdt_cnt_gnd": 0, "wdt_cnt_i2c": 0, "wdt_cnt_can": 0,
            "wdt_cnt_csp": [0, 0], "wdt_gnd_left": 3600,
            "wdt_i2c_left": 600, "wdt_can_left": 600, "wdt_csp_left": [600, 600]}


def _geodetic(position):
    x, y, z = position
    a, e2 = 6378137.0, 6.69437999014e-3
    p = math.hypot(x, y)
    latitude = math.atan2(z, p * (1 - e2))
    for _ in range(8):
        n = a / math.sqrt(1 - e2 * math.sin(latitude) ** 2)
        latitude = math.atan2(z + e2 * n * math.sin(latitude), p)
    n = a / math.sqrt(1 - e2 * math.sin(latitude) ** 2)
    altitude = p / math.cos(latitude) - n if p > 1 else abs(z) - a * math.sqrt(1-e2)
    return math.degrees(latitude), math.degrees(math.atan2(y, x)), altitude


def equipment_snapshot(core_packets: tuple[bytes, ...], *, seed: int = 1,
                       mode: int = 3, power_sample: dict | None = None) -> dict:
    """All screenshot equipment groups, using a named simulation JSON contract."""
    core = {packet["name"]: packet for packet in map(decode, core_packets)}
    eps, attitude, gnss = (core[name]["fields"] for name in SCALAR_FAMILIES[:3])
    stamp_ms = core["EPS_PDU1"]["utc_ms"]
    if power_sample is not None and power_sample.get("utc_s") != stamp_ms//1000:
        raise ValueError("power budget and equipment TM must share the sample time")
    stamp_s = stamp_ms // 1000
    phase = (seed % 10000) * 0.017
    voltage = _wave(stamp_s, 12.0, 0.35, 5800, phase)
    temperature = eps["temperature_c"]
    enabled = lambda count: [True] * count
    device = {"device_type": list(range(8)), "device_status": [1] * 8}
    p60 = {"c_out": [_wave(stamp_s, .12, .025, 470+i*13, phase+i) for i in range(13)],
           "v_out": [5.0] * 7 + [3.3] * 6, "out_en": enabled(13),
           "temp": [temperature, temperature + 1], "bootcause": 0, "bootcnt": 1,
           "uptime": eps["uptime_s"], "resetcause": 0, "batt_mode": 2,
           "heater_on": False, "conv_5v_en": True, "latchup": [0] * 13,
           "vbat_v": voltage, "vcc_c": .03, "batt_c": .23, "batt_v": voltage,
           "batt_temp": [temperature - 1, temperature - 0.5], "dearm_status": 0,
           "batt_chrg": .31, "batt_dischrg": .08,
           "ant6_depl": True, "ar6_depl": True, **device, **_watchdog()}
    def pdu(index):
        return {"c_out": [_wave(stamp_s, .08, .015, 510+i*11, phase+i+index) for i in range(9)],
                "v_out": [eps["bus_voltage_v"]] * 9, "vcc": 3.3, "vbat": voltage,
                "temp": temperature + index, "conv_en": True, "out_en": enabled(9),
                "bootcause": 0, "bootcnt": 1, "uptime": eps["uptime_s"],
                "resetcause": 0, "batt_mode": 2, "latchup": [0] * 9,
                **device, **_watchdog()}
    currents = [_wave(stamp_s, .25, .10, 5800, phase+i*.35) for i in range(6)]
    voltages = [_wave(stamp_s, 4.8, .15, 5700, phase+i*.2) for i in range(6)]
    acu = {"c_in": currents, "v_in": voltages, "vbat": voltage, "vcc": 3.3,
           "temp": [temperature+i*.5 for i in range(3)], "mppt_mode": 1,
           "vboost": [5.2]*6, "power": [a*b for a,b in zip(currents, voltages)],
           "dac_en": enabled(3), "dac_val": [2048]*6, "bootcause": 0, "bootcnt": 1,
           "uptime": eps["uptime_s"], "resetcause": 0, "mppt_time": 60,
           "mppt_period": 5, **device, "wdt_cnt_gnd": 0, "wdt_gnd_left": 3600}
    bpx = {"tcur": .23, "chargercur": .31, "dischargercur": .08, "heater": False,
           "vbatt": voltage, **{f"batt_temp{i+1}": temperature-1+i*.3 for i in range(4)},
           "counter_boot": 1, "bootcause": 0}
    quaternion = [attitude[f"q_{axis}"] for axis in "wxyz"]
    rates = [attitude[f"body_rate_{axis}_rad_s"] for axis in "xyz"]
    cube = {"current_adcs_state": attitude["adcs_state"],
            "adcs_state": {"control_mode": attitude["control_mode"]},
            "adcs_measurements": {"quaternion_wxyz": quaternion, "body_rate_rad_s": rates},
            "power_and_temperature_measurements": [temperature if i % 2 else 3.3 for i in range(32)],
            "actuator_commands": [0.0]*12,
            "t3_estimation_data": quaternion + rates + [0.0]*35,
            "communication_status": [1]*6, "sram_latchup_counters": [0]*6,
            "edac_error_counters": [0]*6, "current_unix_time": [stamp_s]*6,
            "boot_index_and_status": [attitude["boot_index"], 1],
            "adcs_execution_times_ms": [4, 3, 2], "acp_execution_state": 1}
    position = [gnss[f"{axis}_m"] for axis in "xyz"]
    velocity = [gnss[f"v{axis}_mps"] for axis in "xyz"]
    latitude, longitude, altitude = _geodetic(position)
    oem = {"BESTPOS": {"latitude_deg": latitude, "longitude_deg": longitude,
                       "height_m": altitude, "solution_status": gnss["solution_status"]},
           "BESTVEL": {"velocity_itrf_mps": velocity},
           "BESTXYZ": {"position_itrf_m": position, "velocity_itrf_mps": velocity},
           "HWMONITOR": {"temperature_c": temperature + 2, "supply_voltage_v": 3.3},
           "RXSTATUS": {"error_bits": 0, "status_bits": 1},
           "RXSTATUSEVENT": {"event_bits": 0},
           "TIME": {"utc_ms": stamp_ms, "time_status": 1},
           "vendor_binary_available": False,
           "reference_log_bytes": {"BESTPOS": 72, "BESTVEL": 44, "BESTXYZ": 112,
               "HWMONITOR": 84, "RXSTATUS": 88, "RXSTATUSEVENT": 44, "TIME": 44}}
    obc = {"boot_count": 1, "reset_cause": 0, "fsw_image": 1, "crash_count": 0,
           "watchdog_state": 1, "cpu_usage_pct": int(_wave(stamp_s, 22, 5, 300, phase)),
           "ram_usage_pct": 31, "nor_usage_pct": 12, "fsw_version": "SIM-2",
           "execution_state": 1}
    ax = {"temp_brd": temperature+1, "temp_pa": temperature+3,
          "last_rssi": _wave(stamp_s, -94, 3, 900, phase), "last_rferr": 0,
          "tx_count": stamp_s//10, "rx_count": 0, "tx_bytes": 0, "rx_bytes": 0,
          "active_conf": 1, "boot_count": 1, "boot_cause": 0,
          "last_contact": stamp_ms, "bgnd_rssi": -112., "tx_duty": .1,
          "tot_tx_count": stamp_s//10, "tot_rx_count": 0, "tot_tx_bytes": 0, "tot_rx_bytes": 0}
    rails = ("vin", "3v3", "1v8", "1v0", "pa")
    srs = {"tm": {"volt": {key: {"value": value, "fault": False}
                    for key,value in zip(rails, (12.,3.3,1.8,1.,5.))},
                  "curr": {key: {"value": .1, "fault": False} for key in rails},
                  "temp": {key: temperature+i for i,key in enumerate(("psu","mcu","fpga","xcvr","lna","pa"))}},
           "rx": {"frames": 0, "detected": False, "rssi": -112., "freqerr": 0.,
                  "symerr": 0, "ebn0": 0., "carrierlock": False, "framelock": False},
           "frames": 0, "forward_power_w": 0., "bootcount": 1, "gwdt_counter": 0,
           "payload_rx_enabled": False}
    crest = {"voltage_v": 3.3, "current_a": _wave(stamp_s,.16,.02,600,phase),
             "temperature_c": temperature+2, "uart_link_state": 1}
    siren = {"hardware_state": 1, "backup_mode": False, "model_version": 1,
             "fault_code": 0, "risk_score": _wave(stamp_s,.20,.05,780,phase),
             "anomaly_score": _wave(stamp_s,.10,.03,920,phase)}
    equipment = {"P60_Dock": p60, "PDU_200_1": pdu(1), "PDU_200_2": pdu(2),
                 "ACU_200": acu, "BPX": bpx, "CubeADCS": cube, "OEM719A": oem,
                 "A3200": obc, "AX100": ax, "SRS_4": srs, "CREST": crest, "SIREN": siren}
    if power_sample is not None:
        _apply_power_sample(equipment, power_sample)
        mode = power_sample["mode_code"]
    result = {"schema": "pado.sim-full-hk.v1", "non_operational": True,
            "sampled_utc_ms": stamp_ms, "mode": mode,
            "units": "SI; Celsius temperatures; explicit suffixes override",
            "equipment": equipment}
    if power_sample is not None:
        result["power_budget"] = power_sample
    return result


def _apply_power_sample(e: dict, sample: dict) -> None:
    """One physical budget feeds every EPS representation of a TM sample.

    The channel assignment is a simulator display map, not a flight harness.
    P60/PDU views represent upstream/downstream readings of the same devices;
    their readings must not be added together as independent loads.
    """
    voltage = sample["battery_voltage_v"]
    battery_mode = 1 if sample["soc"] < .1 else 2 if sample["soc"] < .25 else \
        4 if sample["soc"] >= .98 else 3
    p60, bpx, acu = (e[key] for key in ("P60_Dock", "BPX", "ACU_200"))
    p60.update(vbat_v=voltage, batt_v=voltage, batt_c=sample["battery_current_a"],
               batt_chrg=sample["charge_current_a"], batt_dischrg=sample["discharge_current_a"],
               batt_mode=battery_mode,
               batt_temp=[sample["battery_temperature_c"],sample["battery_temperature_c"]+.1])
    bpx.update(vbatt=voltage, tcur=sample["battery_current_a"],
               chargercur=sample["charge_current_a"], dischargercur=sample["discharge_current_a"],
               battery_mode=battery_mode)
    bpx.update({f"batt_temp{i+1}":sample["battery_temperature_c"]+i*.1 for i in range(4)})
    loads = sample["device_loads_w"]
    # At a power deficit the model exposes unserved demand. Channel currents
    # show proportional average service rather than claiming every load has
    # switched off while the battery model is still supplying some power.
    served_fraction = max(0.,1.-sample["unserved_load_w"]/sample["load_w"]) \
        if sample["load_w"] > 0 else 1.
    powered = served_fraction > 1e-9
    p60_channels = (("s_band",12.), ("crest",12.), ("gnss_receiver",3.3),
        ("gnss_antenna",3.3), ("uhf",3.3), ("obc",3.3), ("fine_sun_sensor",3.3),
        ("siren",5.), ("adcs_board",voltage), ("reaction_wheel",voltage),
        ("magnetorquer",voltage), ("magnetometer",voltage), ("eps_board",voltage))
    pdu_channels = {
        "PDU_200_1": (("gnss_receiver",3.3), ("gnss_antenna",3.3), ("uhf",3.3),
                      ("obc",3.3), ("fine_sun_sensor",3.3), *[(None,5.)]*4),
        "PDU_200_2": (("s_band",12.), ("crest",12.), ("siren",5.),
                      ("adcs_board",voltage), ("reaction_wheel",voltage),
                      ("magnetorquer",voltage), ("magnetometer",voltage),
                      ("eps_board",voltage), ("battery_auxiliary",voltage))}
    for name, channels in (("P60_Dock",p60_channels), *pdu_channels.items()):
        equipment = e[name]
        equipment["out_en"] = [powered and loads.get(device,0.) > 0 for device,_ in channels]
        equipment["v_out"] = [rail if enabled else 0. for (_,rail),enabled in
                               zip(channels,equipment["out_en"])]
        equipment["c_out"] = [loads.get(device,0.)*served_fraction/rail if enabled and rail>0 else 0.
            for (device,rail),enabled in zip(channels,equipment["out_en"])]
        if name != "P60_Dock":
            equipment.update(vbat=voltage,batt_mode=battery_mode,conv_en=powered)
    acu.update(vbat=voltage, c_in=[0.]*6, v_in=[0.]*6, power=[0.]*6)
    for bank in sample["banks"]:
        channel = bank["channel"]
        if type(channel) is not int or not 0 <= channel < 6:
            raise ValueError("ACU simulation bank channel outside 0..5")
        acu["v_in"][channel] = bank["voltage_v"]
        acu["c_in"][channel] = bank["current_a"]
        acu["power"][channel] = bank["power_w"]
    acu["vboost"] = [voltage if current>0 else 0. for current in acu["c_in"]]
    e["CREST"].update(voltage_v=12. if powered and loads["crest"]>0 else 0.,
                      current_a=loads["crest"]*served_fraction/12. if powered else 0.,
                      uart_link_state=int(powered and loads["crest"]>0))
    e["SIREN"]["hardware_state"] = int(powered and loads["siren"]>0)
    e["OEM719A"]["HWMONITOR"]["supply_voltage_v"] = \
        3.3 if powered and loads["gnss_receiver"]>0 else 0.
    e["SRS_4"]["tm"]["curr"]["vin"]["value"] = loads["s_band"]*served_fraction/12. if powered else 0.
    for key in ("3v3","1v8","1v0","pa"):
        e["SRS_4"]["tm"]["curr"][key]["value"] = 0.


def additional_packets(core_packets: tuple[bytes, ...], next_sequence: int, *,
                       seed: int = 1, include_full: bool = False, mode: int = 3,
                       power_sample: dict | None = None) -> tuple[bytes, ...]:
    snapshot = equipment_snapshot(core_packets, seed=seed, mode=mode, power_sample=power_sample)
    e, stamp = snapshot["equipment"], snapshot["sampled_utc_ms"]
    p60, bpx, acu, obc, ax, siren = (e[name] for name in ("P60_Dock","BPX","ACU_200","A3200","AX100","SIREN"))
    power_fault = (power_sample["unserved_load_w"] > 1e-6 if power_sample is not None else
                   e["PDU_200_1"]["v_out"][0] > 5.5)
    records = [
        ("EPS_P60", {"battery_mode": p60["batt_mode"], "battery_voltage_v": p60["batt_v"],
          "battery_current_a": p60["batt_c"], "battery_temperature_c": p60["batt_temp"][0],
          "channel_enabled_bits": sum(int(on)<<i for i,on in enumerate(p60["out_en"])),
          "latchup_bits": 0, "uptime_s": p60["uptime"]}),
        ("EPS_PDU2", {"channel_enabled_bits": sum(int(on)<<i for i,on in
          enumerate(e["PDU_200_2"]["out_en"])), "latchup_bits": 0,
          "bus_voltage_v": (5. if power_sample["load_w"]-power_sample["unserved_load_w"] > 1e-6 else 0.)
              if power_sample is not None else e["PDU_200_2"]["v_out"][0],
          "temperature_c": e["PDU_200_2"]["temp"],
          "uptime_s": p60["uptime"]}),
        ("EPS_ACU", {key: value for i in range(6) for key,value in
          ((f"input_voltage_{i}_v",acu["v_in"][i]),(f"input_current_{i}_a",acu["c_in"][i]))}),
        ("EPS_BPX", {"battery_voltage_v": bpx["vbatt"],
          **{f"battery_temperature_{i}_c": bpx[f"batt_temp{i+1}"] for i in range(4)},
          "charge_current_a": bpx["chargercur"], "discharge_current_a": bpx["dischargercur"]}),
        ("OBC_HEALTH", {key: obc[key] for key in ("boot_count","reset_cause","fsw_image","crash_count",
           "watchdog_state","cpu_usage_pct","ram_usage_pct","nor_usage_pct")}),
        ("AX100_LINK", {"pa_temperature_c": ax["temp_pa"], "board_temperature_c": ax["temp_brd"],
          "last_rssi_dbm": ax["last_rssi"], "tx_count": ax["tx_count"] & 0xffffffff,
          "rx_count": 0, "boot_count": 1, "last_contact_utc_ms": stamp}),
        ("SIREN_ALERT", {"alert_type": 0, "risk_score": siren["risk_score"],
          "anomaly_score": siren["anomaly_score"], "model_version": 1, "fault_code": 0}),
        ("FDIR_EVENT", {"level": int(power_fault),
          "source_node": 1, "fault_code": (101 if power_sample is not None else 100) if power_fault else 0,
          "action_code": 0, "recovery_state": 0}),
        ("CREST_HEALTH", e["CREST"]),
        ("SIREN_HEALTH", {key:siren[key] for key in ("hardware_state","backup_mode","model_version","fault_code")})]
    if power_sample is not None:
        s = power_sample
        records.insert(4, ("EPS_POWER", {
            "battery_soc_pct":s["soc_pct"], "solar_raw_w":s["solar_raw_w"],
            "solar_bus_w":s["solar_available_w"], "load_w":s["load_w"],
            "net_battery_w":s["battery_voltage_v"]*s["battery_current_a"],
            "battery_voltage_v":s["battery_voltage_v"], "battery_current_a":s["battery_current_a"],
            "charge_current_a":s["charge_current_a"], "discharge_current_a":s["discharge_current_a"],
            "battery_energy_wh":s["stored_energy_wh"], "eclipse":s["eclipse"],
            "unserved_load_w":s["unserved_load_w"], "curtailed_w":s["curtailed_power_w"],
            "conversion_loss_w":s["solar_conversion_loss_w"], "mode":s["mode_code"]}))
    catalog = load_catalog()
    packets = [encode_tm(name, (next_sequence+i)&0xffff, stamp, fields, catalog=catalog)
               for i,(name,fields) in enumerate(records)]
    if include_full:
        data = json.dumps(snapshot, separators=(",",":"), ensure_ascii=True).encode("ascii")
        if len(data) > 160 * (MAX_GENERATED_PDUS-len(packets)-3):
            raise ValueError("full HK simulation JSON exceeds bounded tick")
        count = (len(data)+159)//160
        for i in range(count):
            part = data[i*160:(i+1)*160]
            packets.append(encode_tm("FULL_HK_CHUNK", (next_sequence+len(packets))&0xffff,
                stamp, {"record_id": (stamp//1000) & 0xffffffff or 1, "chunk_index": i,
                    "chunk_count": count, "total_len": len(data), "data_len": len(part),
                    "record_crc32": zlib.crc32(data)&0xffffffff,
                    "data": part.ljust(160,b"\0")}, catalog=catalog))
    return tuple(packets)


def mode_beacon_packets(core_packets: tuple[bytes, ...], next_sequence: int, *,
                        seed: int = 1, mode: int = 3,
                        power_sample: dict | None = None) -> tuple[bytes, bytes]:
    """Compact status follows CORE; equipment values share the same sample time."""
    snapshot = equipment_snapshot(core_packets, seed=seed, mode=mode, power_sample=power_sample)
    mode = snapshot["mode"]
    e = snapshot["equipment"]
    p60, bpx, cube, ax, obc = (e[name] for name in ("P60_Dock","BPX","CubeADCS","AX100","A3200"))
    xyz = e["OEM719A"]["BESTXYZ"]
    fields = {"mode": mode, "format_version": 1,
              "p60_channel_enabled_bits": sum(int(on)<<i for i,on in enumerate(p60["out_en"])),
              "p60_uptime_s": p60["uptime"],
              "p60_boot_count": 1, "battery_charge_current_a": bpx["chargercur"],
              "battery_discharge_current_a": bpx["dischargercur"], "p60_battery_mode": p60["batt_mode"],
              **{key: value for i in range(6) for key,value in
                  ((f"solar_input_voltage_{i}_v",e["ACU_200"]["v_in"][i]),
                   (f"solar_input_current_{i}_a",e["ACU_200"]["c_in"][i]))},
              "battery_voltage_v": bpx["vbatt"],
              **{f"battery_temperature_{i}_c": bpx[f"batt_temp{i+1}"] for i in range(4)},
              "bpx_battery_mode": bpx.get("battery_mode",2),
              "adcs_boot_index": cube["boot_index_and_status"][0],
              "adcs_state": cube["current_adcs_state"], "sram_latchup_count": 0,
              "edac_error_count": 0, "control_mode": cube["adcs_state"]["control_mode"],
              **{f"{axis}_m": xyz["position_itrf_m"][i] for i,axis in enumerate("xyz")},
              **{f"v{axis}_mps": xyz["velocity_itrf_mps"][i] for i,axis in enumerate("xyz")},
              "solution_status": e["OEM719A"]["BESTPOS"]["solution_status"],
              **{f"obc_{key}": obc[key] for key in
                  ("reset_cause","boot_count","fsw_image","crash_count","nor_usage_pct","watchdog_state")},
              "ax100_pa_temperature_c": ax["temp_pa"], "ax100_boot_count": 1,
              "ax100_last_rssi_dbm": ax["last_rssi"],
              "ax100_tx_count": ax["tx_count"] & 0xffffffff, "ax100_rx_count": ax["rx_count"],
              "deployment_flags": 7,
              **{f"body_rate_{axis}_rad_s": cube["adcs_measurements"]["body_rate_rad_s"][i]
                  for i,axis in enumerate("xyz")}}
    stamp = snapshot["sampled_utc_ms"]
    catalog = load_catalog()
    return (encode_tm("MODE_BEACON", next_sequence & 0xffff, stamp, fields, catalog=catalog),
            encode_tm("BEACON_INFO", (next_sequence+1)&0xffff, stamp,
                      {"identity": "SIM_PADO_SAT|pado@khu.ac.kr".ljust(69)}, catalog=catalog))
