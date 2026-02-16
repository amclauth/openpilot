# mactastik

Personal openpilot fork for a 2021 Mazda CX-30 Turbo on a Comma 3X with MoreTorque interceptor.

```
mactastik <- moretore/mazda-frogpilot <- FrogPilot <- openpilot
```

This is a single-vehicle fork. It is not intended for general use.

## Features

### Driving

**Lateral Control + NNFF Rewrite** -- Delay-compensated PID torque control with a standalone neural network feedforward controller, replacing the original lateral controller entirely. Includes 100Hz steering and EPS feedback as a prerequisite. Brought in from FrogPilot main.

**Sticky Integrator Fix** -- PID anti-windup rewritten so the integrator can wind down when error opposes saturation. The old code froze the integrator entirely when output saturated.

### Safety Overrides

**Fire the Babysitter** -- Toggles to disable driver monitoring lockouts, door/seatbelt alerts, overheat alerts, and logging. DM processes can be conditionally disabled entirely.

**Speed Warning Mute** -- Toggle to suppress the >149 km/h speed warning.

### Upload & Connectivity

**Custom Upload Server** -- Uploads dashcam footage to a personal FastAPI server instead of comma.ai. Home screen status widget shows connection state, segment progress, and drive time. Settings sub-panel provides endpoint URL, auth token, and a connection test button.

**Wifi Scan on Park** -- Triggers a wifi scan when the car parks to pick up available networks faster.

### Diagnostics

**OBD-II DTC Scanner** -- Boot-time diagnostic trouble code reader via UDS service 0x19. Queries all standard ECU addresses and stores results in a param. Settings UI has a toggle and a results viewer. The panda switches to ELM327 safety mode when offroad to enable OBD-II queries.

**LEAD_PROXIMITY Signal** -- Renamed and documented the ACC_2 radar signal in the Mazda DBC. Analysis confirmed it represents acceleration margin from the MRCC radar (correlation r = -0.84 with required deceleration), not raw distance.

### UI Fixes

**Longitudinal Feature Gating** -- Added `openpilot_longitudinal_active` flag that hides personality icons, curve speed controller, and other longitudinal UI elements when longitudinal control is not active on the vehicle.

**Ghost Touch Detection** -- Detects and blocks clustered ghost touches at boot, with red markers on screen showing blocked coordinates.

**DMoji Position Fix** -- Fixed widget positioning when driver monitoring is disabled.

**DebugMode Overrides** -- Restored `show_fps` and `onroad_distance_button` gating that was removed in an upstream commit.

**Lead Metrics Fix** -- Fixed operator precedence bug that showed lead info regardless of tuning level.

### Cherry-picks from Upstream

**Model Manager v17** -- Bumped from v16, adding new driving models (MacroStiff, SC driving, WMI). Default changed from Firehose to Dark Souls.

**Lane Width Median** -- Uses median instead of mean for lane width calculation, reducing outlier sensitivity.

**Model List Sync Guard** -- Defensive reset if model lists diverge in length after validation.

**CAN Mode Flag Fix** -- Fixed broken GEN1 torque interceptor detection in panda `main.c` that checked an impossible condition.

## Installation

Enter this URL on the Comma 3X setup screen:

```
https://comma.mclauthlin.com/install/dev
```

The self-hosted installer serves a patched AGNOS binary that clones this fork directly. Fallback via smiskol: `https://smiskol.com/fork/amclauth/dev`

## Upstream References

- [moretore/openpilot](https://github.com/moretore/openpilot) -- Mazda FrogPilot fork (branch: `mazda-frogpilot`)
- [FrogAi/FrogPilot](https://github.com/FrogAi/FrogPilot) -- FrogPilot main
- [commaai/openpilot](https://github.com/commaai/openpilot) -- openpilot
