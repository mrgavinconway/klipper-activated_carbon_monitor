# Moonraker bridge for Klipper Activated Carbon Monitor
#
# Copyright (C) 2026 Gavin Conway
# GPLv3
#
# Mirrors the Klipper activated_carbon_monitor status object into Moonraker's
# native generic sensor interface. Mainsail 2.12+ renders Moonraker sensors in
# the Miscellaneous dashboard panel.

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any, DefaultDict, Deque, Dict, List, Optional, Union


Number = Union[int, float]


class CarbonDashboardSensor:
    def __init__(self, sensor_id: str, friendly_name: str, store_size: int = 1200):
        self.id = sensor_id
        self.name = friendly_name
        self.type = "activated_carbon"
        self.error_state: Optional[str] = None
        self.last_measurements: Dict[str, Number] = {}
        self.last_value: Dict[str, Number] = {}
        self.values: DefaultDict[str, Deque[Number]] = defaultdict(
            lambda: deque(maxlen=store_size)
        )

    async def initialize(self) -> bool:
        logging.info("Registered activated carbon dashboard sensor '%s'", self.name)
        return True

    def set_status(self, status: Dict[str, Any]) -> None:
        measurements: Dict[str, Number] = {}

        def add(name: str, value: Any, scale: float = 1.0) -> None:
            if value is None or isinstance(value, bool):
                return
            if not isinstance(value, (int, float)):
                return
            measurements[name] = float(value) * scale

        add("remaining_percent", status.get("remaining_percent"))
        add("used_percent", status.get("used_percent"))
        add("projected_tvoc_mg_per_h", status.get("estimated_voc_rate_mg_h"))
        add("estimated_voc_generated_mg", status.get("estimated_voc_generated_mg"))
        add("estimated_voc_filtered_mg", status.get("estimated_voc_filtered_mg"))
        add("airflow_percent", status.get("current_airflow_fraction"), 100.0)
        add("airflow_cfm", status.get("current_airflow_cfm"))
        add("active_filter_hours", status.get("active_hours"))
        add("service_usage_hours", status.get("service_usage_hours"))
        add("service_life_hours", status.get("service_life_hours"))
        add("carbon_age_days", status.get("age_days"))
        add("replacement_in_days", status.get("estimated_days_remaining"))

        self.last_measurements = measurements
        self.error_state = None

    def clear(self, error: Optional[str] = None) -> None:
        self.last_measurements = {}
        self.error_state = error

    def _update_sensor_value(self, eventtime: float) -> None:
        for key, value in self.last_measurements.items():
            self.values[key].append(value)
        self.last_value = dict(self.last_measurements)

    def get_sensor_info(self, extended: bool = False) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "id": self.id,
            "friendly_name": self.name,
            "type": self.type,
            "values": self.last_measurements,
        }
        if extended:
            info["parameter_info"] = []
            info["history_fields"] = []
        return info

    def get_sensor_measurements(self) -> Dict[str, List[Number]]:
        return {key: list(values) for key, values in self.values.items()}

    def get_name(self) -> str:
        return self.name

    def close(self) -> None:
        pass


class ActivatedCarbonMonitorBridge:
    def __init__(self, config):
        self.server = config.get_server()
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        self.sensor_manager = self.server.load_component(config, "sensor")

        self.sensor_id = config.get("sensor_name", "activated_carbon")
        self.friendly_name = config.get("friendly_name", "Activated Carbon")
        self.preferred_object = config.get("klipper_object", None)
        self.object_name: Optional[str] = None
        self.callback_registered = False

        store_size = config.getint("sensor_store_size", 1200, minval=60)
        self.sensor = CarbonDashboardSensor(
            self.sensor_id, self.friendly_name, store_size
        )

        if self.sensor_id in self.sensor_manager.sensors:
            raise config.error(
                "Moonraker sensor '%s' already exists" % self.sensor_id
            )
        self.sensor_manager.sensors[self.sensor_id] = self.sensor

        self.server.register_event_handler(
            "server:klippy_ready", self._handle_klippy_ready
        )
        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_klippy_disconnect
        )
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_klippy_shutdown
        )

    async def _handle_klippy_ready(self) -> None:
        objects = await self.klippy_apis.get_object_list([])
        candidates = sorted(
            obj for obj in objects
            if obj == "activated_carbon_monitor"
            or obj.startswith("activated_carbon_monitor ")
        )

        if self.preferred_object:
            if self.preferred_object not in candidates:
                self.sensor.clear(
                    "Configured Klipper carbon monitor object not found"
                )
                self.server.add_warning(
                    "Activated Carbon Monitor: Klipper object '%s' was not found"
                    % self.preferred_object
                )
                return
            selected = self.preferred_object
        elif "activated_carbon_monitor chamber" in candidates:
            selected = "activated_carbon_monitor chamber"
        elif candidates:
            selected = candidates[0]
        else:
            self.sensor.clear("No activated_carbon_monitor Klipper object found")
            self.server.add_warning(
                "Activated Carbon Monitor: no [activated_carbon_monitor ...] "
                "section was found in Klipper"
            )
            return

        self.object_name = selected
        callback = None
        if not self.callback_registered:
            callback = self._handle_status_update
            self.callback_registered = True

        initial = await self.klippy_apis.subscribe_objects(
            {selected: None}, callback=callback, default={}
        )
        if selected in initial:
            self.sensor.set_status(initial[selected])

        logging.info(
            "Activated Carbon Monitor dashboard bridge using Klipper object '%s'",
            selected,
        )

    def _handle_status_update(
        self, status: Dict[str, Dict[str, Any]], eventtime: float
    ) -> None:
        if self.object_name is None:
            return
        update = status.get(self.object_name)
        if update is not None:
            self.sensor.set_status(update)

    async def _handle_klippy_disconnect(self) -> None:
        self.sensor.clear("Klipper disconnected")

    async def _handle_klippy_shutdown(self) -> None:
        self.sensor.clear("Klipper shutdown")

    def close(self) -> None:
        self.sensor.close()


def load_component(config):
    return ActivatedCarbonMonitorBridge(config)
