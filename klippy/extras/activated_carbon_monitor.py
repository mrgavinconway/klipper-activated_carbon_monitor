# Activated carbon service-life monitor for Klipper
#
# Copyright (C) 2026 Gavin Conway
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import logging
import os
import time


class ActivatedCarbonMonitor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split(" ", 1)[1]

        self.carbon_mass_g = config.getfloat("carbon_mass_g", 500.0, above=0.0)
        self.carbon_form = config.get("carbon_form", "pellet")
        self.fan_name = config.get("fan")
        self.rated_cfm = config.getfloat("rated_cfm", 0.0, minval=0.0)
        self.max_equivalent_hours = config.getfloat(
            "max_equivalent_hours", 100.0, above=0.0
        )
        self.state_file = os.path.expanduser(
            config.get(
                "state_file",
                "~/printer_data/config/activated_carbon_monitor.json",
            )
        )
        self.sample_interval = config.getfloat(
            "sample_interval", 5.0, above=0.0
        )

        self.fan = None
        self.current_material = "UNKNOWN"
        self.current_fan_speed = 0.0

        self.active_seconds = 0.0
        self.equivalent_seconds = 0.0
        self.installed_at = int(time.time())
        self.history = []

        self.last_sample_monotonic = None
        self._load_state()

        self.gcode = self.printer.lookup_object("gcode")
        self.gcode.register_mux_command(
            "CARBON_STATUS", "FILTER", self.name,
            self.cmd_CARBON_STATUS,
            desc="Report activated carbon monitor status",
        )
        self.gcode.register_mux_command(
            "CARBON_SET_MATERIAL", "FILTER", self.name,
            self.cmd_CARBON_SET_MATERIAL,
            desc="Set material used by activated carbon monitor",
        )
        self.gcode.register_mux_command(
            "CARBON_REPLACE", "FILTER", self.name,
            self.cmd_CARBON_REPLACE,
            desc="Archive current carbon cartridge and start a new one",
        )

        self.printer.register_event_handler("klippy:connect", self._handle_connect)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)

        self.sample_timer = self.reactor.register_timer(
            self._sample, self.reactor.NEVER
        )

    def _handle_connect(self):
        try:
            self.fan = self.printer.lookup_object(self.fan_name)
        except self.printer.config_error:
            raise self.printer.config_error(
                "activated_carbon_monitor %s: unable to find fan object '%s'"
                % (self.name, self.fan_name)
            )
        now = self.reactor.monotonic()
        self.last_sample_monotonic = now
        self.reactor.update_timer(self.sample_timer, now + self.sample_interval)

    def _handle_shutdown(self):
        self._save_state()

    def _fan_speed(self, eventtime):
        if self.fan is None:
            return 0.0
        try:
            status = self.fan.get_status(eventtime)
        except Exception:
            logging.exception(
                "activated_carbon_monitor %s: fan status read failed", self.name
            )
            return 0.0

        speed = status.get("speed", 0.0)
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, speed))

    def _sample(self, eventtime):
        if self.last_sample_monotonic is None:
            self.last_sample_monotonic = eventtime
            return eventtime + self.sample_interval

        elapsed = max(0.0, eventtime - self.last_sample_monotonic)
        self.last_sample_monotonic = eventtime

        speed = self._fan_speed(eventtime)
        self.current_fan_speed = speed

        if speed > 0.0:
            self.active_seconds += elapsed
            self.equivalent_seconds += elapsed * speed

        self._save_state()
        return eventtime + self.sample_interval

    def _new_state(self):
        return {
            "version": 1,
            "filters": {},
        }

    def _load_state(self):
        data = self._new_state()
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, dict):
                    data = loaded
        except FileNotFoundError:
            pass
        except Exception:
            logging.exception(
                "activated_carbon_monitor %s: failed to load state file %s",
                self.name,
                self.state_file,
            )

        fstate = data.get("filters", {}).get(self.name, {})
        self.active_seconds = float(fstate.get("active_seconds", 0.0))
        self.equivalent_seconds = float(fstate.get("equivalent_seconds", 0.0))
        self.installed_at = int(fstate.get("installed_at", int(time.time())))
        self.current_material = str(fstate.get("current_material", "UNKNOWN"))
        self.history = list(fstate.get("history", []))

    def _save_state(self):
        directory = os.path.dirname(self.state_file)
        if directory:
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception:
                logging.exception(
                    "activated_carbon_monitor %s: failed to create state directory",
                    self.name,
                )
                return

        data = self._new_state()
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
                if isinstance(existing, dict):
                    data = existing
        except FileNotFoundError:
            pass
        except Exception:
            logging.exception(
                "activated_carbon_monitor %s: failed to read existing state before save",
                self.name,
            )

        data.setdefault("version", 1)
        filters = data.setdefault("filters", {})
        filters[self.name] = {
            "active_seconds": self.active_seconds,
            "equivalent_seconds": self.equivalent_seconds,
            "installed_at": self.installed_at,
            "current_material": self.current_material,
            "carbon_mass_g": self.carbon_mass_g,
            "carbon_form": self.carbon_form,
            "rated_cfm": self.rated_cfm,
            "max_equivalent_hours": self.max_equivalent_hours,
            "history": self.history,
            "updated_at": int(time.time()),
        }

        tmp_path = self.state_file + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_path, self.state_file)
        except Exception:
            logging.exception(
                "activated_carbon_monitor %s: failed to save state file %s",
                self.name,
                self.state_file,
            )

    def _remaining_fraction(self):
        used = self.equivalent_seconds / (self.max_equivalent_hours * 3600.0)
        return max(0.0, min(1.0, 1.0 - used))

    def _estimated_air_processed_ft3(self):
        # v0.1 assumes fan speed scales linearly with rated CFM.
        return self.rated_cfm * (self.equivalent_seconds / 60.0)

    def _hours_remaining(self):
        remaining_seconds = (
            self.max_equivalent_hours * 3600.0 - self.equivalent_seconds
        )
        return max(0.0, remaining_seconds / 3600.0)

    def get_status(self, eventtime):
        remaining = self._remaining_fraction()
        now = int(time.time())
        return {
            "remaining_percent": round(remaining * 100.0, 2),
            "used_percent": round((1.0 - remaining) * 100.0, 2),
            "installed_at": self.installed_at,
            "age_days": round(max(0, now - self.installed_at) / 86400.0, 2),
            "active_hours": round(self.active_seconds / 3600.0, 3),
            "equivalent_fullflow_hours": round(
                self.equivalent_seconds / 3600.0, 3
            ),
            "estimated_air_processed_ft3": round(
                self._estimated_air_processed_ft3(), 1
            ),
            "current_fan_speed": round(self.current_fan_speed, 3),
            "current_material": self.current_material,
            "carbon_mass_g": self.carbon_mass_g,
            "carbon_form": self.carbon_form,
            "rated_cfm": self.rated_cfm,
            "estimated_hours_remaining": round(self._hours_remaining(), 2),
        }

    def cmd_CARBON_STATUS(self, gcmd):
        status = self.get_status(self.reactor.monotonic())
        gcmd.respond_info(
            (
                "Activated carbon filter '%s'\n"
                "Remaining: %.2f%%\n"
                "Installed: %s\n"
                "Active filtration: %.2f h\n"
                "Equivalent full-flow: %.2f h\n"
                "Estimated remaining: %.2f h\n"
                "Fan speed: %.0f%%\n"
                "Material: %s"
            )
            % (
                self.name,
                status["remaining_percent"],
                time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(status["installed_at"]),
                ),
                status["active_hours"],
                status["equivalent_fullflow_hours"],
                status["estimated_hours_remaining"],
                status["current_fan_speed"] * 100.0,
                status["current_material"],
            )
        )

    def cmd_CARBON_SET_MATERIAL(self, gcmd):
        material = gcmd.get("MATERIAL").strip().upper()
        if not material:
            raise gcmd.error("MATERIAL must not be empty")
        self.current_material = material
        self._save_state()
        gcmd.respond_info(
            "Activated carbon filter '%s': material set to %s"
            % (self.name, material)
        )

    def cmd_CARBON_REPLACE(self, gcmd):
        now = int(time.time())
        self.history.append({
            "installed_at": self.installed_at,
            "replaced_at": now,
            "active_seconds": self.active_seconds,
            "equivalent_seconds": self.equivalent_seconds,
            "material_at_replacement": self.current_material,
            "carbon_mass_g": self.carbon_mass_g,
            "carbon_form": self.carbon_form,
        })

        self.active_seconds = 0.0
        self.equivalent_seconds = 0.0
        self.installed_at = now
        self.current_material = "UNKNOWN"
        self._save_state()

        gcmd.respond_info(
            "Activated carbon filter '%s': replacement recorded and usage reset"
            % self.name
        )


def load_config_prefix(config):
    return ActivatedCarbonMonitor(config)
