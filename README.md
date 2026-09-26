# Klipper Activated Carbon Monitor

A Klipper extension that estimates activated-carbon service life from the material being printed and the filtration fans actually running.

The design goal is deliberately simple: for a normal installation the user provides the filtration fan names, their full-flow CFM, and the amount of activated carbon installed. The plugin derives print state, nozzle/bed temperatures, an optional chamber temperature, the likely filament family, a conservative projected TVOC emission rate, fan duty, carbon burn-down and a replacement projection.

> This is an estimated service-life model. It is not a direct measurement of VOC concentration or activated-carbon saturation.

## Minimum configuration

```ini
[activated_carbon_monitor chamber]
fans: hepa_left, hepa_right
fan_cfm: 5.0, 5.0
carbon_g: 300
```

`fan_cfm` follows the same order as `fans`. Use the best estimate of airflow through each installed carbon filter at 100% fan speed; measured loaded-filter airflow is preferable to a free-air datasheet rating.

`carbon_g` is the total mass of activated carbon being tracked by this monitor.

The plugin automatically uses:

- `extruder`
- `heater_bed`
- Klipper's `idle_timeout` state to determine whether printing is active
- Klipper `print_stats.filename` as the preferred automatic material signal when the filename contains a recognised material token
- a temperature sensor whose name contains `chamber` or `enclosure`, when available
- fan `speed` and `rpm` reported by Klipper
- built-in material temperature profiles and conservative TVOC baselines
- an active-load service capacity of 50 equivalent hours per 100 g of carbon
- a conservative 60-day calendar-life cap for carbon continuously exposed to air
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

## Mainsail dashboard

The installer also installs a small Moonraker companion component. It mirrors the numeric carbon-monitor values into Moonraker's native sensor API, which Mainsail 2.12+ displays in the **Miscellaneous** dashboard panel.

No Mainsail fork or custom frontend is required.

The **Activated Carbon** sensor intentionally keeps the dashboard concise:

```text
Used percent
Projected TVOC mg per h
Estimated VOC generated mg
Estimated VOC filtered mg
Airflow CFM              (when absolute CFM is configured)
Active filter hours
Service usage hours
Replacement in days
```

Additional internal values remain available through the Klipper status object for calculations, macros and debugging, but are not mirrored into the Mainsail card.

The detected material name and its source remain available through the Klipper object and `CARBON_STATUS`. Mainsail's generic Moonraker sensor widget currently accepts numeric measurements, so the bridge does not encode material names such as `ABS` or `ASA` as arbitrary numbers.

Carbon state is shown in **Miscellaneous**, rather than pretending that carbon remaining or VOC load is a temperature. The Klipper object remains the source of truth; Moonraker only mirrors it for the UI.

## Material detection and projected VOCs

Automatic material detection uses this order:

1. a recognised material token in the current G-code filename;
2. temperature-based inference from nozzle, bed and optional chamber temperatures; and
3. conservative `UNKNOWN` behaviour when no material can be identified.

A manual `CARBON_SET_MATERIAL` override takes precedence over all automatic detection until `CARBON_AUTO_MATERIAL` is called.

### Filename detection

Klipper's standard `print_stats` object exposes the selected G-code filename. The monitor tokenises that filename and recognises material names such as:

```text
ABS
ASA
PLA
PETG
TPU
PC
PA
PA6
PA12
NYLON
HIPS
PVA
```

For example:

```text
EMU_Split_base_Left_Front_ABS_4h22m.gcode
```

is identified as `ABS` with `material_source: filename`, even though its temperatures may also overlap with ASA.

`PA6`, `PA12` and `NYLON` map to the built-in `PA` profile.

### Temperature fallback

When the filename does not contain a recognised material token, the plugin compares the active nozzle target, bed target and available chamber temperature against built-in material profiles.

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

may plausibly match ABS, ASA and other engineering materials. If the filename identifies ABS, ABS wins. Otherwise temperature inference chooses whichever matching candidate has the largest built-in TVOC baseline. The full temperature candidate list is also exposed through `material_candidates`.

## Manual material override

An explicit material can be supplied at any time:

```text
CARBON_SET_MATERIAL FILTER=chamber MATERIAL=ABS
```

`NYLON` is accepted as an alias for `PA`.

A manual override affects **future samples only**. It does not rewrite VOC generation, filtered VOC or service usage already accumulated earlier in the print.

Return to automatic filename/temperature detection with:

```text
CARBON_AUTO_MATERIAL FILTER=chamber
```

## Airflow

By default the plugin only needs the names of the named `[fan_generic ...]` objects that move air through the carbon:

```ini
fans: hepa_left, hepa_right
```

Use a comma-separated list, following normal Klipper list-style configuration. Short names are automatically resolved to `fan_generic <name>`, so the example above resolves to `fan_generic hepa_left` and `fan_generic hepa_right`. Full object names are also accepted if needed.

Klipper fan speed is treated as normalized flow unless a better curve is supplied. This deliberately avoids requiring users to know physical CFM just to use the monitor.

### Optional airflow curve

Real fan airflow through a carbon bed is not normally linear with PWM. A measured or estimated relative curve can be supplied per fan:

```ini
airflow_curve_voron_aire_left: 0:0 20:0 40:0.25 60:0.55 80:0.80 100:1
airflow_curve_voron_aire_right: 0:0 20:0 40:0.25 60:0.55 80:0.80 100:1
```

The right-hand value is relative airflow from 0 to 1.

### Fan CFM

Specify one full-flow CFM value for each configured fan:

```ini
fans: hepa_left, hepa_right
fan_cfm: 5.0, 5.0
```

This enables absolute `current_airflow_cfm` and `air_processed_ft3`.

Use airflow through the installed filter if it has been measured. A free-air datasheet CFM is an upper-bound estimate because activated carbon and cartridge geometry add substantial restriction.

The older `hepa_left@5.0` form remains accepted for compatibility, but `fan_cfm` is the documented form.

## Carbon burn-down model

While a print is active, the detected material selects a projected TVOC baseline.

The plugin accumulates projected emitted VOC mass:

```text
estimated VOC generated += material TVOC mg/h × elapsed print time
```

VOC-rich chamber air can reach exposed carbon even when the filtration fans are stopped, so active printing has a small passive loading floor. The default is 5% of normal forced-flow exposure:

```text
exposure fraction =
    passive_print_load + (1 - passive_print_load) × airflow fraction
```

At the default `passive_print_load: 0.05`, this gives 5% loading with the fans off and 100% loading at full airflow.

The service model then scales carbon usage by:

1. the effective exposure fraction; and
2. the material's TVOC baseline relative to ABS.

When the filter is running outside an active print, a smaller background load is applied because exposed activated carbon continues to age in warm/ambient air.

Active-load capacity scales with the configured amount of carbon. The default is 50 equivalent service hours per 100 g:

```text
active service capacity =
    50 h × carbon_g / 100 g
```

This is intentionally a practical maintenance heuristic rather than a claim that all activated carbon has an identical adsorption capacity.

Carbon also ages while exposed to ambient/chamber air even if the fans are not running. The default calendar limit is 60 days. Calendar ageing and active loading both contribute to the displayed used percentage.

Optional tuning:

```ini
service_life_hours_per_100g: 50
calendar_life_days: 60
background_load: 0.20
passive_print_load: 0.05
projection_window_days: 14
```

## Replacement projection

Daily service usage is persisted. The recent rolling usage rate is used to calculate the active-load replacement projection, while the installation date provides a calendar-life projection from the moment new carbon is installed.

The reported replacement date is the **earlier** of:

```text
active-load exhaustion
calendar-life expiry
```

This prevents a nearly-new cartridge with only a few minutes of recorded use from producing meaningless multi-decade estimates. With default settings, a newly installed cartridge starts at approximately 60 days remaining and heavy/high-VOC use can pull that date closer.

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

### Prerequisites

A normal Klipper installation with Moonraker and a web UI such as Mainsail or Fluidd is expected.

The installer uses the standard locations:

```text
~/klipper
~/moonraker
~/printer_data/config/moonraker.conf
```

Custom paths are supported with installer options.

### Install

SSH to the printer host and run:

```bash
cd ~
git clone https://github.com/mrgavinconway/klipper-activated_carbon_monitor.git
cd klipper-activated_carbon_monitor
./install.sh
```

The installer will:

1. symlink the Klipper module into `~/klipper/klippy/extras/`;
2. symlink the Moonraker companion into `~/moonraker/moonraker/components/`;
3. create a dedicated Moonraker configuration file containing the dashboard bridge and update-manager registration;
4. add a Moonraker `[include ...]` for that file if it is not already present;
5. register this Git repository with Moonraker's `git_repo` update manager; and
6. optionally offer to restart Moonraker and Klipper, with **No** as the default.

The modules are **not copied**. Both Klipper and Moonraker load them through symlinks to the Git checkout, so Moonraker can update the repository in place without a separate reinstall step.

The installer **never restarts Klipper or Moonraker automatically**. In an interactive terminal it asks whether each service should be restarted, defaulting to No. The Klipper prompt explicitly warns that restarting Klipper will stop an active print. In non-interactive use no restart is attempted.

### Moonraker update management

The installer registers the equivalent of:

```ini
[update_manager activated-carbon-monitor]
type: git_repo
channel: dev
path: ~/klipper-activated_carbon_monitor
origin: https://github.com/mrgavinconway/klipper-activated_carbon_monitor.git
primary_branch: main
managed_services: klipper moonraker
```

After restarting Moonraker, **Activated Carbon Monitor** should appear in the update section of Mainsail/Fluidd. Updating it there updates the Git checkout in place and Moonraker restarts both Klipper and Moonraker afterwards so changes to either side of the integration are loaded.

The project currently uses Moonraker's `dev` channel because releases have not yet been tagged with semantic versions. Once stable tagged releases are introduced this can move to a stable release channel.

Moonraker's older `install_script:` update-manager option is intentionally not used. Current Moonraker documentation marks it deprecated for new configurations and does not execute the script during updates.

### Configure the filter

After installation, add the minimum section to `printer.cfg`:

```ini
[activated_carbon_monitor chamber]
fans: hepa_left, hepa_right
fan_cfm: 5.0, 5.0
carbon_g: 300
```

Replace the fan object names, CFM values and carbon mass with those for the installed filtration system.

When the printer is idle, restart Klipper to load the new module. Restart Moonraker to load the dashboard companion and update-manager entry. The installer can prompt for these restarts, but will not perform them without confirmation.

### Non-standard installations

If Klipper or Moonraker use different paths:

```bash
./install.sh -k /path/to/klipper -r /path/to/moonraker -m /path/to/moonraker.conf
```

Run `./install.sh -h` for the available options.

### Existing manual installation

If an earlier version of the plugin was copied manually into `klippy/extras`, clone this repository and run `./install.sh`.

The installer backs up an existing non-symlink `activated_carbon_monitor.py` before replacing it with the managed symlink.

### Uninstall

From the repository:

```bash
./uninstall.sh
```

This removes both the Klipper and Moonraker module symlinks plus the Moonraker component/update-manager registration. It deliberately leaves both the Git repository and the persistent carbon-history JSON in place so uninstalling does not destroy historical data.

Remove the `[activated_carbon_monitor ...]` section from `printer.cfg` before restarting Klipper.

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
