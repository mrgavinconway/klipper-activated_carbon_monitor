# Activated carbon service-life monitor for Klipper
#
# Copyright (C) 2026 Gavin Conway
# GPLv3

import json
import logging
import os
import time


# Baseline TVOC emission rates in mg/h. These are deliberately rounded,
# conservative defaults derived from published chamber studies. They are
# estimates for material classes, not guarantees for an individual spool.
# See docs/RESEARCH.md for sources and rationale.
MATERIAL_PROFILES = {
    "PLA": {"voc_mg_h": 0.23, "nozzle": (180., 235.), "bed": (20., 70.), "chamber": (0., 45.)},
    "PVA": {"voc_mg_h": 0.15, "nozzle": (180., 250.), "bed": (20., 65.), "chamber": (0., 45.)},
    "PC": {"voc_mg_h": 0.18, "nozzle": (250., 315.), "bed": (90., 125.), "chamber": (35., 90.)},
    "PA": {"voc_mg_h": 0.61, "nozzle": (240., 305.), "bed": (40., 105.), "chamber": (25., 90.)},
    "TPU": {"voc_mg_h": 0.75, "nozzle": (205., 245.), "bed": (20., 65.), "chamber": (0., 45.)},
    "HIPS": {"voc_mg_h": 0.89, "nozzle": (225., 275.), "bed": (80., 120.), "chamber": (30., 90.)},
    "ABS": {"voc_mg_h": 1.02, "nozzle": (225., 280.), "bed": (80., 115.), "chamber": (30., 90.)},
    "PETG": {"voc_mg_h": 1.50, "nozzle": (215., 270.), "bed": (55., 95.), "chamber": (0., 55.)},
    "ASA": {"voc_mg_h": 3.00, "nozzle": (235., 275.), "bed": (70., 120.), "chamber": (30., 90.)},
    "UNKNOWN": {"voc_mg_h": 3.00, "nozzle": (0., 1000.), "bed": (0., 1000.), "chamber": (0., 1000.)},
}

REFERENCE_VOC_MG_H = MATERIAL_PROFILES["ABS"]["voc_mg_h"]
DEFAULT_SERVICE_LIFE_HOURS_PER_100G = 50.0
DEFAULT_CARBON_G = 100.0
DEFAULT_CALENDAR_LIFE_DAYS = 60.0
DEFAULT_BACKGROUND_LOAD = 0.20


class ActivatedCarbonMonitor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        parts = config.get_name().split(" ", 1)
        if len(parts) != 2:
            raise config.error("[activated_carbon_monitor] requires a name")
        self.name = parts[1]

        # Minimal configuration is just fans. Everything else has defaults.
        self.fan_specs = self._parse_fans(config.get("fans"))
        self._apply_fan_cfm(config.get("fan_cfm", None))
        self.airflow_curves = {}
        for spec in self.fan_specs:
            raw_curve = config.get("airflow_curve_" + spec["alias"], None)
            self.airflow_curves[spec["alias"]] = self._parse_airflow_curve(raw_curve)
        self.extruder_name = config.get("extruder", "extruder")
        self.bed_name = config.get("bed", "heater_bed")
        self.chamber_name = config.get("chamber_sensor", None)
        self.carbon_g = config.getfloat(
            "carbon_g", DEFAULT_CARBON_G, above=0.)
        self.calendar_life_days = config.getfloat(
            "calendar_life_days", DEFAULT_CALENDAR_LIFE_DAYS, above=0.)
        self.service_life_hours_per_100g = config.getfloat(
            "service_life_hours_per_100g",
            DEFAULT_SERVICE_LIFE_HOURS_PER_100G,
            above=0.)
        # Active-load capacity scales with the amount of installed carbon.
        self.service_life_hours = (
            self.service_life_hours_per_100g * self.carbon_g / 100.0
        )
        self.background_load = config.getfloat(
            "background_load", DEFAULT_BACKGROUND_LOAD, minval=0.)
        self.sample_interval = config.getfloat("sample_interval", 5., above=0.)
        self.save_interval = config.getfloat("save_interval", 60., above=0.)
        self.projection_window_days = config.getfloat(
            "projection_window_days", 14., above=0.)
        self.state_file = os.path.expanduser(config.get(
            "state_file",
            "~/printer_data/config/activated_carbon_monitor.json"))

        self.fans = []
        self.extruder = None
        self.bed = None
        self.chamber = None
        self.idle_timeout = None

        self.current_material = "UNKNOWN"
        self.material_source = "inferred"
        self.current_voc_rate_mg_h = 0.
        self.current_airflow_fraction = 0.
        self.current_airflow_cfm = None
        self.current_fan_speeds = {}
        self.current_fan_rpms = {}

        self.installed_at = int(time.time())
        self.active_seconds = 0.
        self.service_usage_seconds = 0.
        self.estimated_voc_generated_mg = 0.
        self.estimated_voc_filtered_mg = 0.
        self.air_processed_ft3 = 0.
        self.history = []
        self.daily_usage = {}
        self.voc_by_material_mg = {}
        self.last_sample = None
        self.last_save = 0.
        self._load_state()

        self.gcode = self.printer.lookup_object("gcode")
        self.gcode.register_mux_command(
            "CARBON_STATUS", "FILTER", self.name, self.cmd_CARBON_STATUS,
            desc="Report activated carbon monitor status")
        self.gcode.register_mux_command(
            "CARBON_SET_MATERIAL", "FILTER", self.name,
            self.cmd_CARBON_SET_MATERIAL, desc="Override inferred material")
        self.gcode.register_mux_command(
            "CARBON_AUTO_MATERIAL", "FILTER", self.name,
            self.cmd_CARBON_AUTO_MATERIAL,
            desc="Return to temperature-based material inference")
        self.gcode.register_mux_command(
            "CARBON_REPLACE", "FILTER", self.name, self.cmd_CARBON_REPLACE,
            desc="Record activated carbon replacement")

        self.printer.register_event_handler("klippy:connect", self._connect)
        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        self.timer = self.reactor.register_timer(self._sample, self.reactor.NEVER)

    def _parse_fans(self, raw):
        """Parse a comma-separated list of filtration fans.

        Preferred Klipper-style syntax:
          fans: hepa_left, hepa_right

        Short names are resolved to named fan_generic objects at connect time.
        Full Klipper object names remain supported:
          fans: fan_generic hepa_left, fan_generic hepa_right

        Optional absolute full-flow CFM through the installed filter may be
        appended with '@':
          fans: hepa_left@2.0, hepa_right@2.0
        """
        specs = []
        # Accept semicolons from early development versions for compatibility,
        # but document and prefer Klipper's normal comma-separated style.
        normalized = raw.replace("\n", ",").replace(";", ",")
        for item in normalized.split(","):
            item = item.strip()
            if not item:
                continue
            cfm = None
            object_name = item
            if "@" in item:
                object_name, raw_cfm = item.rsplit("@", 1)
                object_name = object_name.strip()
                try:
                    cfm = float(raw_cfm.strip())
                except ValueError:
                    raise self.printer.config_error(
                        "Invalid fan CFM in '%s'" % item)
                if cfm <= 0.:
                    raise self.printer.config_error(
                        "Fan CFM must be greater than zero in '%s'" % item)
            alias = object_name.split()[-1].replace("-", "_")
            specs.append({"alias": alias, "object": object_name, "cfm": cfm})
        if not specs:
            raise self.printer.config_error(
                "At least one filtration fan must be listed in 'fans'")
        return specs

    def _apply_fan_cfm(self, raw):
        """Apply full-flow CFM values in the same order as the fan list."""
        if raw is None:
            return
        values = [
            item.strip() for item in raw.replace("\n", ",").split(",")
            if item.strip()
        ]
        if len(values) != len(self.fan_specs):
            raise self.printer.config_error(
                "fan_cfm must contain one value for each fan in 'fans'")
        for spec, value in zip(self.fan_specs, values):
            try:
                cfm = float(value)
            except ValueError:
                raise self.printer.config_error(
                    "Invalid fan_cfm value '%s'" % value)
            if cfm <= 0.:
                raise self.printer.config_error(
                    "fan_cfm values must be greater than zero")
            spec["cfm"] = cfm

    def _parse_airflow_curve(self, raw):
        """Return PWM->relative-flow points. Default is linear.

        Values on the right hand side are relative flow (0..1), so one curve
        works whether or not the user also supplies full-flow CFM.
        Example: 0:0 20:0 40:0.25 60:0.55 80:0.80 100:1
        """
        if not raw:
            return [(0., 0.), (1., 1.)]
        points = []
        for token in raw.replace(",", " ").split():
            try:
                pwm, flow = token.split(":", 1)
                pwm = float(pwm)
                flow = float(flow)
            except Exception:
                raise self.printer.config_error(
                    "Invalid airflow curve point '%s'" % token)
            if pwm > 1.:
                pwm /= 100.
            points.append((max(0., min(1., pwm)), max(0., min(1., flow))))
        points.sort()
        if points[0][0] > 0.:
            points.insert(0, (0., 0.))
        if points[-1][0] < 1.:
            points.append((1., 1.))
        return points

    def _flow_fraction(self, alias, speed):
        points = self.airflow_curves[alias]
        for idx in range(1, len(points)):
            p0, f0 = points[idx - 1]
            p1, f1 = points[idx]
            if speed <= p1:
                if p1 == p0:
                    return f1
                ratio = (speed - p0) / (p1 - p0)
                return f0 + ratio * (f1 - f0)
        return points[-1][1]

    def _connect(self):
        self.fans = []
        for spec in self.fan_specs:
            requested = spec["object"]
            candidates = [requested]
            # Named generic fans are exposed by Klipper as
            # "fan_generic <name>". Let the user configure just <name>.
            if " " not in requested:
                candidates.append("fan_generic " + requested)

            obj = None
            resolved = None
            for candidate in candidates:
                obj = self.printer.lookup_object(candidate, None)
                if obj is not None:
                    resolved = candidate
                    break

            if obj is None:
                raise self.printer.config_error(
                    "Unable to find filtration fan '%s' "
                    "(tried: %s)" % (requested, ", ".join(candidates)))

            spec["resolved_object"] = resolved
            self.fans.append((spec, obj))

        self.extruder = self.printer.lookup_object(self.extruder_name, None)
        self.bed = self.printer.lookup_object(self.bed_name, None)
        self.idle_timeout = self.printer.lookup_object("idle_timeout", None)
        self.chamber = self._find_chamber_sensor()

        now = self.reactor.monotonic()
        self.last_sample = now
        self.last_save = now
        self.reactor.update_timer(self.timer, now + self.sample_interval)

    def _find_chamber_sensor(self):
        if self.chamber_name:
            return self.printer.lookup_object(self.chamber_name, None)
        heaters = self.printer.lookup_object("heaters", None)
        if heaters is None:
            return None
        try:
            status = heaters.get_status(self.reactor.monotonic())
            names = status.get("available_sensors", [])
        except Exception:
            return None
        preferred = (
            "temperature_sensor chamber",
            "temperature_sensor enclosure",
            "heater_generic chamber",
        )
        lower_map = {name.lower(): name for name in names}
        for name in preferred:
            if name in lower_map:
                return self.printer.lookup_object(lower_map[name], None)
        for name in names:
            lname = name.lower()
            if "chamber" in lname or "enclosure" in lname:
                return self.printer.lookup_object(name, None)
        return None

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

    def _is_printing(self, eventtime):
        if self.idle_timeout is None:
            return False
        try:
            return self.idle_timeout.get_status(eventtime).get("state") == "Printing"
        except Exception:
            return False

    @staticmethod
    def _in_range(value, bounds):
        return bounds[0] <= value <= bounds[1]

    def _infer_material(self, eventtime):
        nozzle, nozzle_target = self._temperature(self.extruder, eventtime)
        bed, bed_target = self._temperature(self.bed, eventtime)
        chamber, _ = self._temperature(self.chamber, eventtime)

        n = nozzle_target if nozzle_target and nozzle_target > 0. else (nozzle or 0.)
        b = bed_target if bed_target and bed_target > 0. else (bed or 0.)
        c = chamber

        if n < 170.:
            return "UNKNOWN", []

        candidates = []
        for material, profile in MATERIAL_PROFILES.items():
            if material == "UNKNOWN":
                continue
            if not self._in_range(n, profile["nozzle"]):
                continue
            if not self._in_range(b, profile["bed"]):
                continue
            if c is not None and not self._in_range(c, profile["chamber"]):
                continue
            candidates.append(material)

        if not candidates:
            return "UNKNOWN", []

        candidates.sort(
            key=lambda name: MATERIAL_PROFILES[name]["voc_mg_h"], reverse=True)
        return candidates[0], candidates

    def _fan_state(self, eventtime):
        speeds = {}
        rpms = {}
        weighted_fraction = 0.
        cfm_total = 0.
        cfm_known = True

        for spec, obj in self.fans:
            try:
                status = obj.get_status(eventtime)
                speed = float(status.get("speed", 0.))
                rpm = status.get("rpm")
            except Exception:
                logging.exception("Carbon monitor: fan read failed")
                speed, rpm = 0., None
            speed = max(0., min(1., speed))
            speeds[spec["alias"]] = speed
            rpms[spec["alias"]] = rpm
            flow_fraction = self._flow_fraction(spec["alias"], speed)
            weighted_fraction += flow_fraction
            if spec["cfm"] is None:
                cfm_known = False
            else:
                cfm_total += spec["cfm"] * flow_fraction

        airflow_fraction = weighted_fraction / float(len(self.fans))
        return airflow_fraction, (cfm_total if cfm_known else None), speeds, rpms

    def _remaining_fraction(self):
        # Carbon can be retired either through active filtration load or simply
        # through prolonged exposure to ambient/chamber air. Use whichever
        # limit is closer to exhaustion.
        active_capacity = self.service_life_hours * 3600.
        active_used = (
            self.service_usage_seconds / active_capacity
            if active_capacity > 0. else 1.0
        )
        age_seconds = max(0., time.time() - self.installed_at)
        calendar_capacity = self.calendar_life_days * 86400.
        calendar_used = (
            age_seconds / calendar_capacity
            if calendar_capacity > 0. else 1.0
        )
        used = max(active_used, calendar_used)
        return max(0., min(1., 1. - used))

    def _record_daily_usage(self, epoch, service_seconds):
        day = time.strftime("%Y-%m-%d", time.localtime(epoch))
        self.daily_usage[day] = self.daily_usage.get(day, 0.) + service_seconds
        cutoff = epoch - int(max(30., self.projection_window_days * 2.) * 86400)
        self.daily_usage = {
            key: value for key, value in self.daily_usage.items()
            if self._date_epoch(key) >= cutoff
        }

    @staticmethod
    def _date_epoch(value):
        try:
            return int(time.mktime(time.strptime(value, "%Y-%m-%d")))
        except Exception:
            return 0

    def _projection(self):
        now = int(time.time())

        # Calendar exposure provides a useful projection from day one and also
        # prevents tiny early usage samples producing absurd multi-decade
        # replacement estimates.
        calendar_expiry = (
            self.installed_at + int(self.calendar_life_days * 86400.)
        )
        calendar_days = max(0., (calendar_expiry - now) / 86400.)

        cutoff = now - int(self.projection_window_days * 86400)
        recent_usage = sum(
            value for key, value in self.daily_usage.items()
            if self._date_epoch(key) >= cutoff)
        elapsed_days = max(1., min(
            self.projection_window_days,
            max(1., (now - self.installed_at) / 86400.)))
        per_day = recent_usage / elapsed_days
        remaining_active = max(
            0., self.service_life_hours * 3600. - self.service_usage_seconds)

        usage_days = None
        if per_day > 0.:
            usage_days = remaining_active / per_day

        if usage_days is None or calendar_days <= usage_days:
            return calendar_days, calendar_expiry

        return usage_days, now + int(usage_days * 86400.)

    def _sample(self, eventtime):
        if self.last_sample is None:
            self.last_sample = eventtime
            return eventtime + self.sample_interval

        elapsed = max(0., eventtime - self.last_sample)
        self.last_sample = eventtime
        epoch = int(time.time())

        airflow_fraction, airflow_cfm, speeds, rpms = self._fan_state(eventtime)
        self.current_airflow_fraction = airflow_fraction
        self.current_airflow_cfm = airflow_cfm
        self.current_fan_speeds = speeds
        self.current_fan_rpms = rpms

        if self.material_source == "inferred":
            self.current_material, _ = self._infer_material(eventtime)

        profile = MATERIAL_PROFILES.get(
            self.current_material, MATERIAL_PROFILES["UNKNOWN"])
        printing = self._is_printing(eventtime)
        voc_rate = profile["voc_mg_h"] if printing else 0.
        self.current_voc_rate_mg_h = voc_rate

        if printing and elapsed > 0.:
            generated = voc_rate * elapsed / 3600.
            self.estimated_voc_generated_mg += generated
            self.voc_by_material_mg[self.current_material] = (
                self.voc_by_material_mg.get(self.current_material, 0.) + generated)
            filtered = generated * airflow_fraction
            self.estimated_voc_filtered_mg += filtered

        if airflow_fraction > 0. and elapsed > 0.:
            self.active_seconds += elapsed
            if airflow_cfm is not None:
                self.air_processed_ft3 += airflow_cfm * elapsed / 60.

            if printing:
                voc_load = max(
                    self.background_load, voc_rate / REFERENCE_VOC_MG_H)
            else:
                voc_load = self.background_load
            service_seconds = elapsed * airflow_fraction * voc_load
            self.service_usage_seconds += service_seconds
            self._record_daily_usage(epoch, service_seconds)

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

        self.installed_at = int(data.get("installed_at", int(time.time())))
        self.active_seconds = float(data.get("active_seconds", 0.))
        self.service_usage_seconds = float(data.get(
            "service_usage_seconds",
            data.get("weighted_fullflow_seconds", 0.)))
        self.estimated_voc_generated_mg = float(
            data.get("estimated_voc_generated_mg", 0.))
        self.estimated_voc_filtered_mg = float(
            data.get("estimated_voc_filtered_mg", 0.))
        self.air_processed_ft3 = float(data.get("air_processed_ft3", 0.))
        self.current_material = str(data.get("current_material", "UNKNOWN"))
        self.material_source = str(data.get("material_source", "inferred"))
        self.history = list(data.get("history", []))
        self.daily_usage = dict(data.get("daily_usage", {}))
        self.voc_by_material_mg = dict(data.get("voc_by_material_mg", {}))

    def _save_state(self):
        directory = os.path.dirname(self.state_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        root = {"version": 3, "filters": {}}
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
                if isinstance(existing, dict):
                    root = existing
        except FileNotFoundError:
            pass
        except Exception:
            logging.exception("Carbon monitor: existing state read failed")

        root["version"] = 3
        filters = root.setdefault("filters", {})
        filters[self.name] = {
            "installed_at": self.installed_at,
            "active_seconds": self.active_seconds,
            "service_usage_seconds": self.service_usage_seconds,
            "estimated_voc_generated_mg": self.estimated_voc_generated_mg,
            "estimated_voc_filtered_mg": self.estimated_voc_filtered_mg,
            "air_processed_ft3": self.air_processed_ft3,
            "current_material": self.current_material,
            "material_source": self.material_source,
            "daily_usage": self.daily_usage,
            "voc_by_material_mg": self.voc_by_material_mg,
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
        days, replacement_epoch = self._projection()
        inferred_candidates = []
        if self.material_source == "inferred":
            _, inferred_candidates = self._infer_material(eventtime)
        return {
            "remaining_percent": round(remaining * 100., 2),
            "used_percent": round((1. - remaining) * 100., 2),
            "installed_at": self.installed_at,
            "age_days": round(max(0., time.time() - self.installed_at) / 86400., 2),
            "active_hours": round(self.active_seconds / 3600., 3),
            "service_usage_hours": round(self.service_usage_seconds / 3600., 3),
            "service_life_hours": self.service_life_hours,
            "service_life_hours_per_100g": self.service_life_hours_per_100g,
            "carbon_g": self.carbon_g,
            "calendar_life_days": self.calendar_life_days,
            "current_material": self.current_material,
            "material_source": self.material_source,
            "material_candidates": inferred_candidates,
            "estimated_voc_rate_mg_h": round(self.current_voc_rate_mg_h, 3),
            "estimated_voc_generated_mg": round(self.estimated_voc_generated_mg, 3),
            "estimated_voc_filtered_mg": round(self.estimated_voc_filtered_mg, 3),
            "voc_by_material_mg": dict(self.voc_by_material_mg),
            "current_airflow_fraction": round(self.current_airflow_fraction, 3),
            "current_airflow_cfm": (
                None if self.current_airflow_cfm is None
                else round(self.current_airflow_cfm, 2)),
            "fan_speeds": dict(self.current_fan_speeds),
            "fan_rpms": dict(self.current_fan_rpms),
            "air_processed_ft3": (
                None if all(spec["cfm"] is None for spec in self.fan_specs)
                else round(self.air_processed_ft3, 1)),
            "estimated_days_remaining": None if days is None else round(days, 1),
            "projected_replacement_at": replacement_epoch,
        }

    def cmd_CARBON_STATUS(self, gcmd):
        status = self.get_status(self.reactor.monotonic())
        replacement = "insufficient usage history"
        if status["projected_replacement_at"]:
            replacement = time.strftime(
                "%Y-%m-%d",
                time.localtime(status["projected_replacement_at"]))
        airflow = "%.0f%%" % (status["current_airflow_fraction"] * 100.)
        if status["current_airflow_cfm"] is not None:
            airflow += " / %.2f CFM" % status["current_airflow_cfm"]
        gcmd.respond_info(
            ("Activated carbon '%s'\n"
             "Remaining: %.2f%%\n"
             "Material: %s (%s)\n"
             "Projected TVOC: %.3f mg/h\n"
             "Estimated TVOC generated: %.2f mg\n"
             "Filter airflow: %s\n"
             "Service usage: %.2f / %.2f h\n"
             "Projected replacement: %s")
            % (self.name, status["remaining_percent"],
               status["current_material"], status["material_source"],
               status["estimated_voc_rate_mg_h"],
               status["estimated_voc_generated_mg"], airflow,
               status["service_usage_hours"], status["service_life_hours"],
               replacement))

    def cmd_CARBON_SET_MATERIAL(self, gcmd):
        material = gcmd.get("MATERIAL").strip().upper()
        if material == "NYLON":
            material = "PA"
        if material not in MATERIAL_PROFILES or material == "UNKNOWN":
            raise gcmd.error("Unknown MATERIAL '%s'" % material)
        self.current_material = material
        self.material_source = "manual"
        self._save_state()
        gcmd.respond_info(
            "Carbon material override: %s (%.2f mg/h TVOC baseline)" %
            (material, MATERIAL_PROFILES[material]["voc_mg_h"]))

    def cmd_CARBON_AUTO_MATERIAL(self, gcmd):
        self.material_source = "inferred"
        self.current_material, candidates = self._infer_material(
            self.reactor.monotonic())
        self._save_state()
        suffix = ""
        if len(candidates) > 1:
            suffix = " (conservative choice from %s)" % ", ".join(candidates)
        gcmd.respond_info(
            "Carbon material inference enabled: %s%s" %
            (self.current_material, suffix))

    def cmd_CARBON_REPLACE(self, gcmd):
        now = int(time.time())
        self.history.append({
            "installed_at": self.installed_at,
            "replaced_at": now,
            "active_seconds": self.active_seconds,
            "service_usage_seconds": self.service_usage_seconds,
            "estimated_voc_generated_mg": self.estimated_voc_generated_mg,
            "estimated_voc_filtered_mg": self.estimated_voc_filtered_mg,
            "air_processed_ft3": self.air_processed_ft3,
            "voc_by_material_mg": dict(self.voc_by_material_mg),
            "remaining_percent": round(self._remaining_fraction() * 100., 2),
            "carbon_g": self.carbon_g,
            "calendar_life_days": self.calendar_life_days,
        })
        self.installed_at = now
        self.active_seconds = 0.
        self.service_usage_seconds = 0.
        self.estimated_voc_generated_mg = 0.
        self.estimated_voc_filtered_mg = 0.
        self.air_processed_ft3 = 0.
        self.daily_usage = {}
        self.voc_by_material_mg = {}
        self.material_source = "inferred"
        self.current_material = "UNKNOWN"
        self._save_state()
        gcmd.respond_info("Carbon replacement recorded for '%s'" % self.name)


def load_config_prefix(config):
    return ActivatedCarbonMonitor(config)
