# Research basis

This document records the evidence used for the built-in filament VOC baselines and the activated-carbon service-life model.

The numbers in the plugin are intentionally rounded, conservative maintenance defaults. They should not be interpreted as universal emission factors for every filament brand, colour, printer or extrusion temperature.

## Primary source: consolidated chamber tests

**Zhang, Q. and Black, M.S. (2023). _Exposure hazards of particles and volatile organic compounds emitted from material extrusion 3D printing: Consolidation of chamber study data_. Environment International 182, 108316.**

DOI: https://doi.org/10.1016/j.envint.2023.108316

Open copy used during development:

https://www.kora3d.com/downloads/3d%20Printer%20Safety%20Study%20CIRI.pdf

The paper consolidates standardized chamber-testing data and includes 58 VOC emission evaluations.

The text reports median TVOC emission rates of approximately:

| Material | Median TVOC |
|---|---:|
| PLA | 0.23 mg/h |
| Nylon / PA | 0.61 mg/h |
| ABS | 1.02 mg/h |

The material distributions also show that formulation matters substantially. High-emission tests occurred for several materials, including ASA and PETG, with some evaluations above 3 mg/h.

The study also reinforces that different polymers have different characteristic VOC mixtures. Examples include styrene for ABS/HIPS/ASA, caprolactam for nylon and lactide for PLA.

This is the primary reason the plugin models material-specific projected VOC output rather than treating all printing hours equally.

## Davis et al. 2019

**Davis, A.Y. et al. (2019). _Characterization of volatile organic compound emissions from consumer level material extrusion 3D printers_. Building and Environment 160, 106209.**

DOI: https://doi.org/10.1016/j.buildenv.2019.106209

Reported average TVOC emission rates included approximately:

| Material | TVOC |
|---|---:|
| PVA | 0.147 mg/h |
| PLA | 0.193 mg/h |
| ABS | 0.835 mg/h |
| HIPS | 0.888 mg/h |

The study also demonstrated temperature dependence. In one comparison, increasing nozzle temperature from 230 °C to 255 °C increased TVOC by about 25% and styrene by about 27%.

This supports two design choices:

1. temperature is useful context for the emissions model; and
2. a single universal emission value cannot be treated as an exact physical measurement.

## Azimi et al. 2016

**Azimi, P. et al. (2016). _Emissions of Ultrafine Particles and Volatile Organic Compounds from Commercially Available Desktop Three-Dimensional Printers with Multiple Filaments_. Environmental Science & Technology.**

DOI: https://doi.org/10.1021/acs.est.5b04983

The reported total VOC emission rates varied substantially by printer/material combination, from only a few micrograms per minute for some combinations to almost 200 µg/min for a high-emitting nylon case.

A low-emitting PC combination was around 3 µg/min, or about 0.18 mg/h. ABS styrene emissions also varied substantially between printer combinations.

The variability is one reason the plugin calls its values baselines rather than constants.

## Wojnowski et al. 2022

**Wojnowski, W. et al. (2022). _Emission Profiles of Volatiles during 3D Printing with ABS, ASA, Nylon, and PETG Polymer Filaments_. Molecules 27(12), 3814.**

DOI: https://doi.org/10.3390/molecules27123814

This study found ABS to be the highest-emitting of the tested filament samples, with styrene dominant. Nylon and PETG were substantially lower in those particular tests, and ASA's styrene emission was below the ABS sample.

That does not contradict the plugin's conservative PETG/ASA baselines: it demonstrates that individual formulations can differ markedly. The larger 2023 consolidated data set contains higher-emitting PETG and ASA evaluations.

For maintenance estimation, the plugin deliberately chooses a conservative representative baseline rather than the lowest reported sample.

## Selected built-in baselines

The shipped service-model values are:

| Material | Baseline TVOC | Basis |
|---|---:|---|
| PVA | 0.15 mg/h | Davis 2019 average |
| PC | 0.18 mg/h | Approx. 3 µg/min low-emitting PC result in Azimi 2016 |
| PLA | 0.23 mg/h | Zhang & Black 2023 reported median |
| PA / Nylon | 0.61 mg/h | Zhang & Black 2023 reported median |
| TPU | 0.75 mg/h | Conservative rounded value informed by consolidated distribution |
| HIPS | 0.89 mg/h | Davis 2019 average |
| ABS | 1.02 mg/h | Zhang & Black 2023 reported median |
| PETG | 1.50 mg/h | Conservative rounded value informed by consolidated distribution and observed variability |
| ASA | 3.00 mg/h | Conservative rounded high-emission baseline informed by consolidated distribution |
| UNKNOWN | 3.00 mg/h | Deliberately conservative fallback |

PETG, ASA and TPU therefore have less precise baselines than PLA, ABS and nylon. They should be revisited as larger standardized datasets become available.

## Temperature ranges and inference

The built-in temperature windows are broad operational ranges assembled from common manufacturer profiles and slicer guidance rather than exact polymer-identification boundaries.

Current defaults:

| Material | Nozzle °C | Bed °C | Chamber °C |
|---|---:|---:|---:|
| PLA | 180–235 | 20–70 | 0–45 |
| PVA | 180–250 | 20–65 | 0–45 |
| PC | 250–315 | 90–125 | 35–90 |
| PA | 240–305 | 40–105 | 25–90 |
| TPU | 205–245 | 20–65 | 0–45 |
| HIPS | 225–275 | 80–120 | 30–90 |
| ABS | 225–280 | 80–115 | 30–90 |
| PETG | 215–270 | 55–95 | 0–55 |
| ASA | 235–275 | 70–120 | 30–90 |

Useful reference material includes manufacturer guidance from Prusa and Bambu Lab. These sources show substantial overlap between practical printing temperatures, which is why temperature alone cannot positively identify a filament.

### Conservative crossover rule

Inference works by building the set of all materials whose nozzle, bed and available chamber ranges match the active printer temperatures.

If two or more materials match, the plugin chooses the material with the **highest built-in TVOC baseline**.

This is intentional. For example, a 260 °C nozzle and hot bed may fit several engineering filaments. The service-life monitor should not choose the cleaner candidate merely because it is possible.

The chosen material and complete candidate list are exposed to Klipper.

## Why the model does not convert VOC milligrams directly to carbon saturation

Activated-carbon adsorption capacity is not one fixed mg-of-VOC-per-g-of-carbon value.

Published adsorption capacities for individual compounds can be high under controlled laboratory conditions, but real printer filtration contains:

- mixtures of VOC species
- changing concentrations
- humidity
- elevated chamber/filter temperatures
- finite bed depth and contact time
- continuously exposed carbon
- carbon with manufacturer-specific chemistry and pore structure

Competitive adsorption can substantially change capacity compared with single-compound testing.

Therefore the current model does **not** claim:

```text
X mg emitted = Y% of carbon exhausted
```

Instead it uses projected material VOC output to scale an empirical service-life baseline.

## 50-hour service-life default

The default `service_life_hours: 50` is based on practical printer-filtration maintenance guidance rather than theoretical maximum adsorption capacity.

Nevermore documentation has historically used an approximately 50-hour print-time replacement estimate for carbon in hot enclosed-printer use, while also noting that heat and simple exposure to room air age activated carbon.

The plugin therefore uses 50 equivalent full-flow service hours as a conservative starting point and applies:

- material-dependent VOC loading during printing; and
- a lower background ageing load while the filtration fans are running without an active print.

This default should become better calibrated as real cartridge histories are collected.

## Airflow

Klipper exposes fan command speed and, where a tachometer is configured, RPM. It cannot derive physical CFM through a carbon bed from those values alone.

For zero-configuration operation, the plugin uses normalized fan airflow. Users who have measured their installed filter can optionally supply:

- a PWM-to-relative-flow curve; and/or
- full-flow CFM.

Absolute CFM is used for reporting air volume, but is not required for the service-life estimate.

## Future validation

The strongest future improvement is direct VOC feedback.

An upstream/downstream sensor arrangement could measure:

```text
inlet VOC -> carbon bed -> outlet VOC
```

and estimate breakthrough as outlet concentration rises relative to inlet concentration.

That would allow real cartridge behaviour to calibrate:

- material emission baselines
- airflow assumptions
- empirical service life
- replacement thresholds

Until then the plugin should continue to label all calculated VOC and carbon-life values as estimates.
