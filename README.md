# Klipper Activated Carbon Monitor

A Klipper extension for tracking activated-carbon filter usage and exposing the remaining estimated service life directly through Klipper.

## Goals

- Expose carbon state as a native Klipper status object.
- Persist usage across Klipper restarts.
- Track when carbon was installed/replaced.
- Track fan-duty-weighted filtration time.
- Accept material information explicitly, with temperature-based inference planned.
- Keep the service-life model configurable and replaceable as better data becomes available.

## Status

Early development / experimental.

The initial implementation intentionally uses a simple service-life model. It is **not** a direct measurement of activated-carbon saturation.

## Installation

Copy `klippy/extras/activated_carbon_monitor.py` into your Klipper installation:

```bash
~/klipper/klippy/extras/activated_carbon_monitor.py
```

Then add a configuration section:

```ini
[activated_carbon_monitor chamber]
carbon_mass_g: 500
carbon_form: pellet
fan: fan_generic bed_fans
rated_cfm: 24
max_equivalent_hours: 100
state_file: ~/printer_data/config/activated_carbon_monitor.json
```

Restart Klipper.

## Klipper status object

The extension exposes:

```text
printer["activated_carbon_monitor chamber"]
```

Example fields:

```text
remaining_percent
used_percent
installed_at
age_days
active_hours
equivalent_fullflow_hours
estimated_air_processed_ft3
current_fan_speed
current_material
estimated_hours_remaining
```

## G-code

### Query

```text
CARBON_STATUS FILTER=chamber
```

### Set current material

```text
CARBON_SET_MATERIAL FILTER=chamber MATERIAL=ABS
```

### Replace carbon

```text
CARBON_REPLACE FILTER=chamber
```

This archives the previous cartridge in the state file and starts a new cartridge from 100%.

## Service-life model

v0.1 accumulates two time values:

- **active hours**: elapsed time while the configured fan is running.
- **equivalent full-flow hours**: elapsed time multiplied by fan duty.

For example, one hour at 50% fan contributes:

```text
0.5 equivalent full-flow hours
```

The initial estimated remaining percentage is:

```text
remaining = 1 - equivalent_fullflow_hours / max_equivalent_hours
```

This is intentionally conservative and simple. Planned versions will incorporate:

- measured PWM-to-CFM airflow curves
- carbon mass and geometry
- material-specific emission factors
- filament consumed
- nozzle / bed / chamber temperature material inference
- historical burn-down projections
- optional VOC-sensor feedback

## Important limitation

Activated-carbon exhaustion cannot be determined accurately from fan runtime alone. VOC species, concentration, humidity, carbon chemistry, bed depth, residence time and temperature all affect adsorption. This project therefore reports an **estimated service life**, not a laboratory measurement of carbon saturation.

## Licence

GPL-3.0
