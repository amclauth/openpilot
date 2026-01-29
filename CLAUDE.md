# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

openpilot/FrogPilot - An open-source advanced driver assistance system (ADAS) that upgrades driver assistance in 300+ supported cars. This is a FrogPilot fork with experimental features.

## Build Commands

```bash
# Build (uses SCons)
scons -j$(nproc)

# Build options
scons --minimal      # Minimal build (no tests/tools)
scons --extras       # Full development build
scons --asan         # Address sanitizer
scons --ubsan        # Undefined behavior sanitizer
scons --compile_db   # Generate compile_commands.json
```

## Testing

```bash
# Run all tests (parallel execution with xdist)
pytest

# Run single test file
pytest selfdrive/car/mazda/tests/test_mazda.py

# Run single test function
pytest selfdrive/car/tests/test_car_interfaces.py::test_interface_attrs

# Run tests excluding slow ones
pytest -m "not slow"

# Run with verbose output
pytest -v
```

## Linting & Type Checking

```bash
# Lint (ruff)
ruff check .
ruff format .

# Type check
mypy .

# Pre-commit hooks (runs all checks)
pre-commit run --all-files
```

## Code Style

- **Python indent**: 2 spaces
- **Line length**: 160 characters
- **Import style**: Use `openpilot.` prefix for internal imports

  ```python
  from openpilot.selfdrive.car import CarInterface
  from openpilot.common.params import Params
  ```

- **Test markers**: `@pytest.mark.slow`, `@pytest.mark.tici` (device-only)

## Architecture

### Directory Structure

- `selfdrive/` - Core ADAS logic
  - `car/` - Car manufacturer implementations (16 brands)
  - `controls/` - Control algorithms
  - `modeld/` - Neural network driving models
  - `ui/` - Qt-based user interface
- `system/` - System services (hardware, logging, camera, sensors)
- `common/` - Shared utilities
- `frogpilot/` - FrogPilot-specific customizations
- `cereal/` - Message definitions (Cap'n Proto schemas)
- `opendbc/` - CAN database files (git submodule)
- `panda/` - Hardware interface (git submodule)

### Car Implementation Pattern

Each car brand follows this structure in `selfdrive/car/<brand>/`:

| File | Purpose |
|------|---------|
| `interface.py` | CarInterface class - main entry point |
| `carstate.py` | Parse CAN messages to CarState |
| `carcontroller.py` | Generate CAN commands for controls |
| `values.py` | Car-specific constants and configs |
| `fingerprints.py` | ECU firmware signatures for identification |
| `<brand>can.py` | CAN message encoding/decoding helpers |

### Message System

Uses cereal (Cap'n Proto) + msgq (ZMQ-based) for inter-process communication. Message definitions in `cereal/log.capnp`.

### Hardware Targets

- `larch64` - TICI (comma 3/3X device)
- `x86_64` / `aarch64` - PC development
- `Darwin` - macOS

## Git Submodules

Key submodules that need initialization: `panda`, `opendbc`, `cereal`, `msgq_repo`, `rednose_repo`, `tinygrad_repo`

```bash
git submodule update --init --recursive
```

## Directory Index

Detailed index of all directories (2 levels deep) for faster navigation.

### selfdrive/ - Core ADAS Logic

`selfdrive/assets/` - Media assets: fonts, icons, images, sounds, training data for UI
`selfdrive/car/` - Vehicle interfaces for 15+ brands (Honda, Toyota, Ford, GM, Hyundai, Mazda, Nissan, Subaru, Tesla, VW, Chrysler, BMW, etc.)
`selfdrive/car/<brand>/` - Brand-specific: carstate.py, carcontroller.py, interface.py, values.py, fingerprints.py
`selfdrive/car/tests/` - Cross-brand car interface tests
`selfdrive/controls/` - Core control algorithms: lateral (steering), longitudinal (accel/brake), planning
`selfdrive/controls/lib/` - Control libraries: drive helpers, events, latcontrol, longcontrol, vehicle model
`selfdrive/debug/` - Debugging utilities
`selfdrive/locationd/` - Localization daemon: GPS + sensor fusion for positioning
`selfdrive/modeld/` - ML model inference: neural network runners, transforms, vision parsing
`selfdrive/modeld/models/` - Model definitions and runners
`selfdrive/monitoring/` - Driver monitoring and attention detection
`selfdrive/navd/` - Navigation daemon
`selfdrive/pandad/` - Panda hardware communication daemon (CAN bus interface)
`selfdrive/test/` - Integration tests: longitudinal maneuvers, process replay
`selfdrive/ui/` - Qt-based user interface
`selfdrive/ui/qt/` - Qt widget implementations
`selfdrive/ui/translations/` - UI localization files

### system/ - System Services

`system/athena/` - Cloud connectivity: remote access, registration, device communication
`system/camerad/` - Camera daemon: video capture, sensor integration, snapshots
`system/camerad/cameras/` - Camera sensor implementations
`system/camerad/sensors/` - Sensor-specific code
`system/hardware/` - Hardware abstraction layer (PC, TICI), fan control, power monitoring
`system/hardware/pc/` - PC-specific hardware interface
`system/hardware/tici/` - TICI (comma device) hardware interface
`system/logcatd/` - Android logcat daemon
`system/loggerd/` - Logging daemon: driving data recording, video encoding
`system/loggerd/encoders/` - Video encoder implementations
`system/manager/` - Process manager: supervises all system daemons
`system/proclogd/` - Process logging for performance monitoring
`system/qcomgpsd/` - Qualcomm GPS daemon
`system/sensord/` - Sensor daemon: IMU and vehicle sensor integration
`system/sensord/sensors/` - Individual sensor implementations
`system/tests/` - System-level tests
`system/ubloxd/` - u-blox GPS receiver daemon
`system/updated/` - Software update daemon (casync delta updates)
`system/webrtc/` - WebRTC daemon for remote video/audio streaming

### frogpilot/ - FrogPilot Customizations

`frogpilot/assets/` - Visual assets: themes, icons, model metadata
`frogpilot/assets/holiday_themes/` - Seasonal UI themes
`frogpilot/assets/model_metadata/` - ML model metadata
`frogpilot/assets/nnff_models/` - Neural network feedforward models
`frogpilot/assets/toggle_icons/` - UI toggle icons
`frogpilot/classic_modeld/` - Classic neural network backend
`frogpilot/classic_modeld/models/` - Classic ML models
`frogpilot/classic_modeld/runners/` - Model inference runners
`frogpilot/common/` - FrogPilot-specific utilities and variables
`frogpilot/controls/` - FrogPilot control algorithm customizations
`frogpilot/controls/lib/` - Speed limit control, curve speed controller, event handlers
`frogpilot/navigation/` - Navigation and route planning features
`frogpilot/system/` - System-level utilities
`frogpilot/system/the_pond/` - External telemetry integration
`frogpilot/third_party/` - Bundled Python libraries (certifi, dateutil, influxdb_client, etc.)
`frogpilot/tinygrad_modeld/` - Tinygrad neural network backend
`frogpilot/tools/` - Development utilities
`frogpilot/ui/` - UI customizations
`frogpilot/ui/qt/` - Qt-based GUI additions
`frogpilot/ui/screenrecorder/` - Screen recording utilities

### common/ - Shared Utilities

`common/api/` - Public API interfaces
`common/mock/` - Mock implementations for testing
`common/tests/` - Common utility tests
`common/transformations/` - Coordinate transforms: camera calibration, orientation, geometry

### cereal/ - Messaging Protocol

`cereal/include/` - C++ headers for message definitions
`cereal/messaging/` - Pub/sub messaging (ZMQ, msgq backends)
`cereal/messaging/tests/` - Messaging system tests

### msgq_repo/ - Message Queue

`msgq_repo/msgq/` - Lock-free message queue core (shared memory ring buffers)
`msgq_repo/msgq/logger/` - Logging backend
`msgq_repo/msgq/tests/` - MSGQ tests
`msgq_repo/msgq/visionipc/` - Vision IPC for large buffer transfers (images/video)

### opendbc/ - CAN Database

`opendbc/can/` - CAN message utilities and parsing
`opendbc/can/tests/` - CAN utility tests
`opendbc/generator/` - DBC file generation by manufacturer
`opendbc/generator/chrysler/` - Chrysler DBC generators
`opendbc/generator/gm/` - GM DBC generators
`opendbc/generator/honda/` - Honda DBC generators
`opendbc/generator/hyundai/` - Hyundai DBC generators
`opendbc/generator/nissan/` - Nissan DBC generators
`opendbc/generator/subaru/` - Subaru DBC generators
`opendbc/generator/tesla/` - Tesla DBC generators
`opendbc/generator/toyota/` - Toyota DBC generators

### panda/ - Hardware Interface

`panda/board/` - STM32 embedded firmware
`panda/board/boards/` - Board configs for panda hardware revisions
`panda/board/drivers/` - CAN, USB, SPI peripheral drivers
`panda/board/safety/` - Safety mode implementations per vehicle brand
`panda/board/stm32f4/` - STM32F413/F423 code
`panda/board/stm32h7/` - STM32H725 code
`panda/certs/` - Certificates and crypto keys
`panda/crypto/` - Cryptographic functions
`panda/drivers/` - Host-side drivers
`panda/drivers/linux/` - Linux socket CAN driver
`panda/drivers/spi/` - SPI communication driver
`panda/python/` - Python userspace library
`panda/tests/` - Unit and integration tests
`panda/tests/hitl/` - Hardware-in-the-loop tests
`panda/tests/safety/` - Safety mode tests per vehicle type

### rednose_repo/ - Kalman Filter

`rednose_repo/rednose/` - Extended Kalman filter with symbolic Jacobians, MSCKF
`rednose_repo/rednose/helpers/` - Filter design helpers
`rednose_repo/examples/` - Example filter implementations

### third_party/ - External Dependencies

`third_party/acados/` - Acados optimal control solver
`third_party/catch2/` - Catch2 C++ testing framework
`third_party/json11/` - JSON parsing library
`third_party/libyuv/` - Image conversion library
`third_party/maplibre-native-qt/` - MapLibre Qt bindings for map rendering
`third_party/opencl/` - OpenCL GPU computing headers
`third_party/qrcode/` - QR code generation
`third_party/qt5/` - Qt5 framework
`third_party/snpe/` - Qualcomm SNPE neural network framework

### Other Top-Level

`body/` - Comma body robotics firmware (motor control, actuators)
`body/board/` - Embedded firmware for body control
`docs/` - Documentation
`release/` - Release artifacts
`scripts/` - Build and utility scripts
`site_scons/` - SCons build system configuration
`teleoprtc_repo/` - WebRTC teleoperation library
`tinygrad_repo/` - TinyGrad ML framework
`tools/` - Development and testing tools
