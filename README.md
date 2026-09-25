# Klipper Activated Carbon Monitor

A Klipper extension that estimates activated-carbon service life from **carbon mass, material, actual filtration fan state and configured airflow curves**, persists cartridge history, and exposes the result as a native Klipper status object.

> This is an estimated service-life model, not a direct measurement of carbon saturation.

## Current features

- Native Klipper object: `printer["activated_carbon_monitor chamber"]`
- Persistent carbon install/replacement date and cartridge history
- Multiple filtration fans
- Per-fan PWM -> CFM curves with interpolation
- Actual accumulated air volume
- Material-specific loading factors
- Automatic material inference from nozzle, bed and optional chamber temperatures
- Manual material override from slicer/G-code
- Carbon mass scaling
- Persistent daily burn-down data
- Projected replacement date based on recent usage
- Atomic state-file writes

## Example configuration

```ini
[activated_carbon_monitor chamber]
carbon_mass_g: 500
carbon_form: pellet

# alias=Klipper object,rated free/full-flow CFM
# Separate fans with semicolons.
fans: left=fan_generic voron_aire_left,24; right=fan_generic voron_aire_right,24

# PWM:measured-CFM points. Percent or 0..1 PWM values are accepted.
# Ideally measure these through the actual carbon cartridge.
airflow_curve_left: 0:0 20:2.8 40:7.1 60:12.4 80:17.9 100:21.2
airflow_curve_right: 0:0 20:2.7 40:7.0 60:12.2 80:17.7 100:21.0

extruder: extruder
bed: heater_bed
chamber_sensor: temperature_sensor chamber

# Service-life calibration. 500g at the default 20h/100g = 100
# material-weighted equivalent full-flow hours.
capacity_hours_per_100g: 20

# Relative loading multipliers. These are configurable model assumptions,
# not claimed adsorption constants.
material_factors:
    PLA=0.15 PETG=0.30 TPU=0.25 ABS=1.00 ASA=0.90 PC=0.80 PA=0.65 UNKNOWN=1.00

projection_window_days: 14
sample_interval: 5
save_interval: 60
state_file: ~/printer_data/config/activated_carbon_monitor.json
```

## Material detection

The monitor uses target/actual nozzle, bed and optional chamber temperatures to infer a broad material class. Temperature alone cannot reliably distinguish materials with overlapping print profiles (notably ABS vs ASA, or PLA vs TPU).

For reliable classification, set the material from slicer start G-code:

```text
CARBON_SET_MATERIAL FILTER=chamber MATERIAL=ABS
```

To return to automatic inference:

```text
CARBON_AUTO_MATERIAL FILTER=chamber
```

When inference cannot classify a profile it uses `UNKNOWN`, whose factor defaults to 1.0.

## Airflow model

Every configured fan has a PWM-to-CFM curve. CFM between points is linearly interpolated.

The monitor accumulates:

```text
air_processed += current_cfm * elapsed_time
```

and carbon burn-down:

```text
weighted_usage += elapsed_time
                  * (current_total_cfm / configured_max_total_cfm)
                  * material_factor
```

This means two fans are handled independently before their airflow is combined. Running a fan at 50% PWM does **not** automatically mean 50% airflow when a measured curve is configured.

## Capacity model

Capacity currently scales with carbon mass:

```text
capacity =
    capacity_hours_per_100g
    * (carbon_mass_g / 100)
```

The default 20 hours/100g is a starting calibration value only. Real adsorption life varies with carbon chemistry, pellet geometry, VOC species/concentration, humidity, temperature, bed depth and residence time.

The project's design intentionally keeps the capacity model configurable so real-world data and future VOC sensing can improve it without changing the Klipper-facing API.

## Projection

Daily weighted consumption is persisted. The monitor uses the recent `projection_window_days` consumption rate to calculate:

- estimated days remaining
- projected replacement timestamp/date

If there is insufficient recorded consumption, the projected date is reported as unavailable instead of inventing one.

## Klipper status fields

```text
remaining_percent
used_percent
installed_at
age_days
active_hours
weighted_fullflow_hours
air_processed_ft3
current_airflow_cfm
fan_speeds
current_material
material_source
material_factor
carbon_mass_g
carbon_form
estimated_days_remaining
projected_replacement_at
```

Example:

```jinja
{printer["activated_carbon_monitor chamber"].remaining_percent}
{printer["activated_carbon_monitor chamber"].projected_replacement_at}
```

## Commands

```text
CARBON_STATUS FILTER=chamber
CARBON_SET_MATERIAL FILTER=chamber MATERIAL=ABS
CARBON_AUTO_MATERIAL FILTER=chamber
CARBON_REPLACE FILTER=chamber
```

`CARBON_REPLACE` archives the previous cartridge statistics, records the replacement time and begins a new burn-down at 100%.

## Persistence

Default:

```text
~/printer_data/config/activated_carbon_monitor.json
```

Usage is periodically persisted using an atomic temporary-file replacement and is also saved during Klipper shutdown.

## Next calibration work

The architecture now supports the requested model. The important next step is obtaining defensible defaults:

1. Measure or estimate the actual CFM through each installed Voron-Aire/carbon cartridge at several fan speeds.
2. Establish initial service-life calibration for the carbon being used.
3. Feed the slicer's actual material into `CARBON_SET_MATERIAL` where possible.
4. Compare predicted exhaustion against VOC measurements or observed breakthrough and adjust the capacity/material factors.

## Licence

GPL-3.0
