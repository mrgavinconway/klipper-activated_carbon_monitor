# Activated carbon service-life monitor for Klipper
#
# Copyright (C) 2026 Gavin Conway
# GPLv3

import json
import logging
import os
import time


DEFAULT_MATERIAL_FACTORS = {
    "UNKNOWN": 1.00,
    "PLA": 0.15,
    "PETG": 0.30,
    "TPU": 0.25,
    "ABS": 1.00,
    "ASA": 0.90,
    "PC": 0.80,
    "PA": 0.65,
}


class ActivatedCarbonMonitor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        parts = config.get_name().split(" ", 1)
        if len(parts) != 2:
            raise config.error("[activated_carbon_monitor] requires a name")
        self.name = parts[1]

        self.carbon_mass_g = config.getfloat("carbon_mass_g", 500., above=0.)
        self.carbon_form = config.get("carbon_form", "pellet")
        # Capacity is deliberately expressed as weighted full-flow hours per
        # 100g. It is a service-life estimate, not adsorption chemistry.
        self.capacity_hours_per_100g = config.getfloat(
            "capacity_hours_per_100g", 20., above=0.)
        self.sample_interval = config.getfloat("sample_interval", 5., above=0.)
        self.save_interval = config.getfloat("save_interval", 60., above=0.)
        self.projection_window_days = config.getfloat(
            "projection_window_days", 14., above=0.)

        self.state_file = os.path.expanduser(config.get(
            "state_file",
            "~/printer_data/config/activated_carbon_monitor.json"))

        self.fan_specs = self._parse_fans(config.get("fans"))
        self.airflow_curves = {}
        for spec in self.fan_specs:
            curve = config.get("airflow_curve_" + spec["alias"], None)
            self.airflow_curves[spec["alias"]] = self._parse_curve(
                curve, spec["rated_cfm"])

        self.extruder_name = config.get("extruder", "extruder")
        self.bed_name = config.get("bed", "heater_bed")
        self.chamber_name = config.get("chamber_sensor", None)

        self.material_factors = dict(DEFAULT_MATERIAL_FACTORS)
        factors = config.get("material_factors", None)
        if factors:
            for token in factors.replace("\n", " ").split():
                if "=" not in token:
                    continue
                name, value = token.split("=", 1)
                self.material_factors[name.strip().upper()] = float(value)

        self.fans = []
        self.extruder = None
        self.bed = None
        self.chamber = None
        self.current_material = "UNKNOWN"
        self.material_source = "inferred"
        self.current_airflow_cfm = 0.
        self.current_fan_speeds = {}
        self.active_seconds = 0.
        self.weighted_fullflow_seconds = 0.
        self.air_processed_ft3 = 0.
        self.installed_at = int(time.time())
        self.history = []
        self.daily_usage = {}
        self.last_sample = None
        self.last_save = 0.
        self._load_state()

        self.gcode = self.printer.lookup_object("gcode")
        self.gcode.register_mux_command(
            "CARBON_STATUS", "FILTER", self.name, self.cmd_CARBON_STATUS,
            desc="Report activated carbon status")
        self.gcode.register_mux_command(
            "CARBON_SET_MATERIAL", "FILTER", self.name,
            self.cmd_CARBON_SET_MATERIAL, desc="Override material")
        self.gcode.register_mux_command(
            "CARBON_AUTO_MATERIAL", "FILTER", self.name,
            self.cmd_CARBON_AUTO_MATERIAL, desc="Return to temperature inference")
        self.gcode.register_mux_command(
            "CARBON_REPLACE", "FILTER", self.name, self.cmd_CARBON_REPLACE,
            desc="Record activated carbon replacement")

        self.printer.register_event_handler("klippy:connect", self._connect)
        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        self.timer = self.reactor.register_timer(self._sample, self.reactor.NEVER)

    def _parse_fans(self, raw):
        # Syntax: alias=klipper object,rated_cfm ; alias2=object,rated_cfm
        specs = []
        for item in raw.split(";"):
            item = item.strip()
            if not item:
                continue
            try:
                alias, rest = item.split("=", 1)
                obj, cfm = rest.rsplit(",", 1)
                specs.append({"alias": alias.strip(), "object": obj.strip(),
                              "rated_cfm": float(cfm)})
            except Exception:
                raise self.printer.config_error(
                    "Invalid fans entry '%s'; expected alias=object,rated_cfm" % item)
        if not specs:
            raise self.printer.config_error("At least one filtration fan is required")
        return specs

    def _parse_curve(self, raw, rated_cfm):
        # PWM:CFM pairs; endpoints are added when absent.
        if not raw:
            return [(0., 0.), (1., rated_cfm)]
        pts = []
        for token in raw.replace(",", " ").split():
            pwm, cfm = token.split(":", 1)
            p = float(pwm)
            if p > 1.:
                p /= 100.
            pts.append((max(0., min(1., p)), max(0., float(cfm))))
        pts.sort()
        if not pts or pts[0][0] > 0.:
            pts.insert(0, (0., 0.))
        if pts[-1][0] < 1.:
            pts.append((1., rated_cfm))
        return pts

    def _curve_cfm(self, alias, speed):
        pts = self.airflow_curves[alias]
        for idx in range(1, len(pts)):
            p0, c0 = pts[idx - 1]
            p1, c1 = pts[idx]
            if speed <= p1:
                if p1 == p0:
                    return c1
                ratio = (speed - p0) / (p1 - p0)
                return c0 + ratio * (c1 - c0)
        return pts[-1][1]

    def _connect(self):
        self.fans = []
        for spec in self.fan_specs:
            try:
                obj = self.printer.lookup_object(spec["object"])
            except Exception:
                raise self.printer.config_error(
                    "Unable to find filtration fan '%s'" % spec["object"])
            self.fans.append((spec, obj))
        self.extruder = self.printer.lookup_object(self.extruder_name, None)
        self.bed = self.printer.lookup_object(self.bed_name, None)
        if self.chamber_name:
            self.chamber = self.printer.lookup_object(self.chamber_name, None)
        now = self.reactor.monotonic()
        self.last_sample = now
        self.last_save = now
        self.reactor.update_timer(self.timer, now + self.sample_interval)

    def _shutdown(self):
        self._save_state()

    def _temperature(self, obj, eventtime):
        if obj is None:
            return None, None
        try:
            status = obj.get_status(eventtime)
            return status.get("temperature"), status.get("target")
        except Exception:
            return None, None

    def _infer_material(self, eventtime):
        nozzle, nozzle_target = self._temperature(self.extruder, eventtime)
        bed, bed_target = self._temperature(self.bed, eventtime)
        chamber, _ = self._temperature(self.chamber, eventtime)
        n = nozzle_target or nozzle or 0.
        b = bed_target or bed or 0.
        c = chamber or 0.
        # Conservative broad classes. ABS vs ASA cannot reliably be separated
        # by temperature, so the higher configured factor is used.
        if n >= 235. and b >= 90.:
            material = "ABS"
            if self.material_factors.get("ASA", 0) > self.material_factors["ABS"]:
                material = "ASA"
            return material
        if n >= 245. and b < 90.:
            return "PETG"
        if n >= 225. and b >= 65.:
            return "PETG"
        if 180. <= n <= 235. and b <= 70. and c < 40.:
            return "PLA"
        return "UNKNOWN"

    def _fan_state(self, eventtime):
        total = 0.
        speeds = {}
        max_total = 0.
        for spec, obj in self.fans:
            try:
                speed = float(obj.get_status(eventtime).get("speed", 0.))
            except Exception:
                logging.exception("Carbon monitor: fan read failed")
                speed = 0.
            speed = max(0., min(1., speed))
            speeds[spec["alias"]] = speed
            total += self._curve_cfm(spec["alias"], speed)
            max_total += self._curve_cfm(spec["alias"], 1.)
        return total, max_total, speeds

    def _capacity_seconds(self):
        mass_units = self.carbon_mass_g / 100.
        return self.capacity_hours_per_100g * mass_units * 3600.

    def _remaining_fraction(self):
        cap = self._capacity_seconds()
        return max(0., min(1., 1. - self.weighted_fullflow_seconds / cap))

    def _record_daily_usage(self, epoch, weighted_seconds):
        day = time.strftime("%Y-%m-%d", time.localtime(epoch))
        self.daily_usage[day] = self.daily_usage.get(day, 0.) + weighted_seconds
        cutoff = epoch - int(max(30., self.projection_window_days * 2.) * 86400)
        self.daily_usage = {
            k: v for k, v in self.daily_usage.items()
            if self._date_epoch(k) >= cutoff
        }

    def _date_epoch(self, value):
        try:
            return int(time.mktime(time.strptime(value, "%Y-%m-%d")))
        except Exception:
            return 0

    def _projection(self):
        now = int(time.time())
        cutoff = now - int(self.projection_window_days * 86400)
        used = sum(v for k, v in self.daily_usage.items()
                   if self._date_epoch(k) >= cutoff)
        elapsed_days = max(1., min(
            self.projection_window_days,
            max(1., (now - self.installed_at) / 86400.)))
        per_day = used / elapsed_days
        remaining = max(0., self._capacity_seconds() -
                        self.weighted_fullflow_seconds)
        if per_day <= 0.:
            return None, None
        days = remaining / per_day
        return days, now + int(days * 86400)

    def _sample(self, eventtime):
        if self.last_sample is None:
            self.last_sample = eventtime
            return eventtime + self.sample_interval
        elapsed = max(0., eventtime - self.last_sample)
        self.last_sample = eventtime

        airflow, max_airflow, speeds = self._fan_state(eventtime)
        self.current_airflow_cfm = airflow
        self.current_fan_speeds = speeds
        if self.material_source == "inferred":
            self.current_material = self._infer_material(eventtime)
        factor = self.material_factors.get(
            self.current_material, self.material_factors["UNKNOWN"])

        if airflow > 0.:
            self.active_seconds += elapsed
            self.air_processed_ft3 += airflow * elapsed / 60.
            flow_fraction = airflow / max_airflow if max_airflow > 0. else 0.
            weighted = elapsed * flow_fraction * factor
            self.weighted_fullflow_seconds += weighted
            self._record_daily_usage(int(time.time()), weighted)

        if eventtime - self.last_save >= self.save_interval:
            self._save_state()
            self.last_save = eventtime
        return eventtime + self.sample_interval

    def _load_state(self):
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh).get("filters", {}).get(self.name, {})
        except FileNotFoundError:
            data = {}
        except Exception:
            logging.exception("Carbon monitor: state load failed")
            data = {}
        self.active_seconds = float(data.get("active_seconds", 0.))
        self.weighted_fullflow_seconds = float(
            data.get("weighted_fullflow_seconds",
                     data.get("equivalent_seconds", 0.)))
        self.air_processed_ft3 = float(data.get("air_processed_ft3", 0.))
        self.installed_at = int(data.get("installed_at", int(time.time())))
        self.current_material = str(data.get("current_material", "UNKNOWN"))
        self.material_source = str(data.get("material_source", "inferred"))
        self.history = list(data.get("history", []))
        self.daily_usage = dict(data.get("daily_usage", {}))

    def _save_state(self):
        directory = os.path.dirname(self.state_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        root = {"version": 2, "filters": {}}
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
                if isinstance(existing, dict):
                    root = existing
        except FileNotFoundError:
            pass
        except Exception:
            logging.exception("Carbon monitor: existing state read failed")
        root["version"] = 2
        filters = root.setdefault("filters", {})
        filters[self.name] = {
            "installed_at": self.installed_at,
            "active_seconds": self.active_seconds,
            "weighted_fullflow_seconds": self.weighted_fullflow_seconds,
            "air_processed_ft3": self.air_processed_ft3,
            "current_material": self.current_material,
            "material_source": self.material_source,
            "daily_usage": self.daily_usage,
            "history": self.history,
            "updated_at": int(time.time()),
        }
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(root, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, self.state_file)
        except Exception:
            logging.exception("Carbon monitor: state save failed")

    def get_status(self, eventtime):
        remaining = self._remaining_fraction()
        days, epoch = self._projection()
        return {
            "remaining_percent": round(remaining * 100., 2),
            "used_percent": round((1. - remaining) * 100., 2),
            "installed_at": self.installed_at,
            "age_days": round(max(0., time.time() - self.installed_at) / 86400., 2),
            "active_hours": round(self.active_seconds / 3600., 3),
            "weighted_fullflow_hours": round(
                self.weighted_fullflow_seconds / 3600., 3),
            "air_processed_ft3": round(self.air_processed_ft3, 1),
            "current_airflow_cfm": round(self.current_airflow_cfm, 2),
            "fan_speeds": dict(self.current_fan_speeds),
            "current_material": self.current_material,
            "material_source": self.material_source,
            "material_factor": self.material_factors.get(
                self.current_material, self.material_factors["UNKNOWN"]),
            "carbon_mass_g": self.carbon_mass_g,
            "carbon_form": self.carbon_form,
            "estimated_days_remaining": None if days is None else round(days, 1),
            "projected_replacement_at": epoch,
        }

    def cmd_CARBON_STATUS(self, gcmd):
        s = self.get_status(self.reactor.monotonic())
        replacement = "insufficient usage history"
        if s["projected_replacement_at"]:
            replacement = time.strftime(
                "%Y-%m-%d", time.localtime(s["projected_replacement_at"]))
        gcmd.respond_info(
            ("Activated carbon '%s'\nRemaining: %.2f%%\nMaterial: %s (%s, x%.2f)"
             "\nAirflow: %.2f CFM\nActive: %.2f h\nWeighted usage: %.2f h"
             "\nAir processed: %.0f ft3\nProjected replacement: %s")
            % (self.name, s["remaining_percent"], s["current_material"],
               s["material_source"], s["material_factor"],
               s["current_airflow_cfm"], s["active_hours"],
               s["weighted_fullflow_hours"], s["air_processed_ft3"],
               replacement))

    def cmd_CARBON_SET_MATERIAL(self, gcmd):
        material = gcmd.get("MATERIAL").strip().upper()
        if material not in self.material_factors:
            raise gcmd.error("Unknown MATERIAL '%s'" % material)
        self.current_material = material
        self.material_source = "manual"
        self._save_state()
        gcmd.respond_info("Carbon material override: %s" % material)

    def cmd_CARBON_AUTO_MATERIAL(self, gcmd):
        self.material_source = "inferred"
        self.current_material = self._infer_material(self.reactor.monotonic())
        self._save_state()
        gcmd.respond_info("Carbon material inference enabled: %s" %
                          self.current_material)

    def cmd_CARBON_REPLACE(self, gcmd):
        now = int(time.time())
        self.history.append({
            "installed_at": self.installed_at,
            "replaced_at": now,
            "active_seconds": self.active_seconds,
            "weighted_fullflow_seconds": self.weighted_fullflow_seconds,
            "air_processed_ft3": self.air_processed_ft3,
            "remaining_percent": round(self._remaining_fraction() * 100., 2),
        })
        self.installed_at = now
        self.active_seconds = 0.
        self.weighted_fullflow_seconds = 0.
        self.air_processed_ft3 = 0.
        self.daily_usage = {}
        self.material_source = "inferred"
        self.current_material = "UNKNOWN"
        self._save_state()
        gcmd.respond_info("Carbon replacement recorded for '%s'" % self.name)


def load_config_prefix(config):
    return ActivatedCarbonMonitor(config)
