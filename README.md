# Klipper Activated Carbon Monitor

A Klipper extension that estimates activated-carbon service life from the material being printed and the filtration fans actually running.

The design goal is deliberately simple: for a normal installation the user should only need to identify the filtration fan(s). The plugin derives print state, nozzle/bed temperatures, an optional chamber temperature, the likely filament family, a conservative projected TVOC emission rate, fan duty, carbon burn-down and a replacement projection.

> This is an estimated service-life model. It is not a direct measurement of VOC concentration or activated-carbon saturation.

## Minimum configuration

```ini
[activated_carbon_monitor chamber]
fans: fan_generic voron_aire_left; fan_generic voron_aire_right
```

That is enough to enable the default model.

The plugin automatically uses:

- `extruder`
- `heater_bed`
- Klipper's `idle_timeout` state to determine whether printing is active
- a temperature sensor whose name contains `chamber` or `enclosure`, when available
- fan `speed` and `rpm` reported by Klipper
- built-in material temperature profiles and conservative TVOC baselines
- a 50-hour equivalent service-life baseline
- a 14-day rolling usage window for the replacement-date projection
- persistent state in `~/printer_data/config/activated_carbon_monitor.json`

## What it exposes to Klipper

The native status object is:

```jinja
printer["activated_carbon_monitor chamber"]
```

Fields include:

```text
remaining_percent
used_percent
installed_at
age_days

current_material
material_source
material_candidates

estimated_voc_rate_mg_h
estimated_voc_generated_mg
estimated_voc_filtered_mg
voc_by_material_mg

current_airflow_fraction
current_airflow_cfm
fan_speeds
fan_rpms
air_processed_ft3

active_hours
service_usage_hours
service_life_hours

estimated_days_remaining
projected_replacement_at
```

Example:

```jinja
{printer["activated_carbon_monitor chamber"].remaining_percent}
{printer["activated_carbon_monitor chamber"].current_material}
{printer["activated_carbon_monitor chamber"].estimated_voc_rate_mg_h}
```

## Material inference and projected VOCs

The plugin compares the active nozzle target, bed target and available chamber temperature against built-in material profiles.

If more than one filament is plausible at the same temperatures, it intentionally chooses the plausible material with the **higher baseline TVOC emission rate**. This is a conservative service-life rule, not a claim that the printer has positively identified the polymer.

Built-in defaults:

| Material | Nozzle °C | Bed °C | Chamber °C | Baseline TVOC mg/h |
|---|---:|---:|---:|---:|
| PVA | 180–250 | 20–65 | 0–45 | 0.15 |
| PC | 250–315 | 90–125 | 35–90 | 0.18 |
| PLA | 180–235 | 20–70 | 0–45 | 0.23 |
| PA / Nylon | 240–305 | 40–105 | 25–90 | 0.61 |
| TPU | 205–245 | 20–65 | 0–45 | 0.75 |
| HIPS | 225–275 | 80–120 | 30–90 | 0.89 |
| ABS | 225–280 | 80–115 | 30–90 | 1.02 |
| PETG | 215–270 | 55–95 | 0–55 | 1.50 |
| ASA | 235–275 | 70–120 | 30–90 | 3.00 |

`UNKNOWN` uses 3.00 mg/h so an unclassified active print is not treated optimistically.

The values are rounded service-model baselines derived from published chamber-emission research. They are not universal emission constants for every spool or printer. Filament formulation, colour, additives, extrusion temperature and printer geometry can materially change emissions. See [docs/RESEARCH.md](docs/RESEARCH.md).

### Example conservative overlap

A profile such as:

```text
Nozzle: 260 °C
Bed:    105 °C
Chamber: 50 °C
```

may plausibly match ABS, ASA and other engineering materials. The plugin chooses whichever matching candidate has the largest built-in TVOC baseline. The full candidate list is also exposed through `material_candidates`.

## Manual material override

Temperature inference is the default, but slicer G-code can provide an exact material when desired:

```text
CARBON_SET_MATERIAL FILTER=chamber MATERIAL=ABS
```

`NYLON` is accepted as an alias for `PA`.

Return to automatic inference with:

```text
CARBON_AUTO_MATERIAL FILTER=chamber
```

## Airflow

By default the plugin needs no fan specification other than the Klipper object names:

```ini
fans: fan_generic voron_aire_left; fan_generic voron_aire_right
```

Klipper fan speed is treated as normalized flow unless a better curve is supplied. This deliberately avoids requiring users to know physical CFM just to use the monitor.

### Optional airflow curve

Real fan airflow through a carbon bed is not normally linear with PWM. A measured or estimated relative curve can be supplied per fan:

```ini
airflow_curve_voron_aire_left: 0:0 20:0 40:0.25 60:0.55 80:0.80 100:1
airflow_curve_voron_aire_right: 0:0 20:0 40:0.25 60:0.55 80:0.80 100:1
```

The right-hand value is relative airflow from 0 to 1.

### Optional absolute CFM

If the installed filter's actual full-flow CFM is known, append it with `@`:

```ini
fans: fan_generic voron_aire_left@1.6; fan_generic voron_aire_right@1.5
```

This enables absolute `current_airflow_cfm` and `air_processed_ft3`. It is not required for carbon-life tracking.

Do not use a fan's free-air datasheet CFM unless that is genuinely representative of airflow through the installed carbon cartridge.

## Carbon burn-down model

While a print is active, the inferred material selects a projected TVOC baseline.

The plugin accumulates projected emitted VOC mass:

```text
estimated VOC generated += material TVOC mg/h × elapsed print time
```

It then scales carbon service usage by:

1. current relative filter airflow; and
2. the material's TVOC baseline relative to ABS.

When the filter is running outside an active print, a smaller background load is applied because exposed activated carbon continues to age in warm/ambient air.

The default reference service life is 50 equivalent hours. This is intentionally a practical printer-maintenance baseline rather than a conversion from laboratory adsorption capacity in mg/g.

Optional tuning:

```ini
service_life_hours: 50
background_load: 0.20
projection_window_days: 14
```

## Replacement projection

Daily service usage is persisted. The recent rolling usage rate is used to calculate:

```text
estimated_days_remaining
projected_replacement_at
```

A printer used heavily for higher-emitting materials burns down faster than one used occasionally for PLA.

If insufficient usage has been recorded, no date is fabricated.

## Commands

```text
CARBON_STATUS FILTER=chamber
CARBON_SET_MATERIAL FILTER=chamber MATERIAL=ABS
CARBON_AUTO_MATERIAL FILTER=chamber
CARBON_REPLACE FILTER=chamber
```

`CARBON_REPLACE` archives the completed cartridge and resets the current cartridge to 100%.

History includes:

- install timestamp
- replacement timestamp
- active filter time
- service-usage time
- projected VOC generated
- projected VOC filtered/presented to the filtration system
- per-material projected VOC totals
- known air volume where CFM has been configured
- remaining percentage at replacement

## Installation

Copy:

```text
klippy/extras/activated_carbon_monitor.py
```

into:

```text
~/klipper/klippy/extras/
```

Add the minimum configuration shown above and restart Klipper.

## Scientific limitations

This project intentionally favours conservative maintenance estimates over false precision.

The following cannot be known from Klipper temperatures and fan PWM alone:

- exact filament formulation
- actual chamber VOC concentration
- individual VOC species and concentrations
- single-pass adsorption efficiency
- carbon chemistry and remaining adsorption sites
- effects of humidity, temperature and competing VOCs
- real airflow through a specific cartridge unless measured

For that reason the output is an **estimated service-life monitor**. A future upstream/downstream VOC-sensor mode can use actual measured breakthrough to calibrate or supersede the model.

See [docs/RESEARCH.md](docs/RESEARCH.md) for the research basis and source selection.

## Licence

GPL-3.0
