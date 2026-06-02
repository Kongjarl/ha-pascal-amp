"""Constants for the Pascal IP Amplifier integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "pascal_amp"

# --- Connection -----------------------------------------------------------
DEFAULT_PORT: Final = 7621
DEFAULT_NAME: Final = "Pascal Amplifier"

# Connection / command timing
CONNECT_TIMEOUT: Final = 10.0
COMMAND_TIMEOUT: Final = 5.0
GET_ALL_TIMEOUT: Final = 20.0
RECONNECT_MIN_DELAY: Final = 1.0
RECONNECT_MAX_DELAY: Final = 30.0
# Coordinator heartbeat / safety poll interval (seconds)
HEARTBEAT_INTERVAL: Final = 30
# Debounce for coalescing pushed register updates into one HA state write
PUSH_DEBOUNCE: Final = 0.4
# Dynamic (level meter) subscribe frequency. 1 == ~1 update/sec per the API doc.
SUBSCRIBE_DYN_FREQ: Final = 1

CONF_NAME: Final = "name"

# --- Registers ------------------------------------------------------------
REG_API_VERSION: Final = "API_VERSION"
REG_STATE: Final = "SYSTEM.STATUS.STATE"
REG_SIGNAL_IN: Final = "SYSTEM.STATUS.SIGNAL_IN"
REG_SIGNAL_OUT: Final = "SYSTEM.STATUS.SIGNAL_OUT"
REG_LAN: Final = "SYSTEM.STATUS.LAN"
REG_WIFI: Final = "SYSTEM.STATUS.WIFI"

REG_VENDOR: Final = "SYSTEM.DEVICE.VENDOR_NAME"
REG_MODEL: Final = "SYSTEM.DEVICE.MODEL_NAME"
REG_SERIAL: Final = "SYSTEM.DEVICE.SERIAL"
REG_FIRMWARE: Final = "SYSTEM.DEVICE.FIRMWARE"
REG_FIRMWARE_DATE: Final = "SYSTEM.DEVICE.FIRMWARE_DATE"
REG_MAC: Final = "SYSTEM.DEVICE.MAC"
REG_DEVICE_NAME: Final = "SETUP.SYSTEM.DEVICE_NAME"

# Amplifier states (SYSTEM.STATUS.STATE)
STATE_INIT: Final = "INIT"
STATE_STANDBY: Final = "STANDBY"
STATE_ON: Final = "ON"
STATE_FAULT: Final = "FAULT"

# Default zone gain bounds when the amplifier does not report them
DEFAULT_GAIN_MIN: Final = -80.0
DEFAULT_GAIN_MAX: Final = 0.0
# dB step used for volume up/down via the INC command
VOLUME_STEP_DB: Final = 2.0

# Source id used to silence a zone (Unused Input)
SOURCE_OFF_ID: Final = 0
SOURCE_OFF_NAME: Final = "Off"

# Mix sources start at SID 500 -> Mix 1 (see {SID} Input Source in the API doc)
MIX_SOURCE_BASE: Final = 500

# --- Control ranges (from the API doc) ------------------------------------
OUT_GAIN_MIN: Final = -30.0
OUT_GAIN_MAX: Final = 15.0
IN_GAIN_MIN: Final = -15.0
IN_GAIN_MAX: Final = 15.0
# The noise generator input (IID 400) uses a different gain range.
GENERATOR_IID: Final = 400
GEN_GAIN_MIN: Final = -48.0
GEN_GAIN_MAX: Final = 0.0

GAIN_STEP: Final = 0.5

# Input sensitivity options (IN-{IID}.SENS)
SENS_OPTIONS: Final = ["14DBU", "4DBU", "-10DBV", "MIC"]

# Input EQ band types (IN-{IID}.EQ-{EID}.TYPE)
INPUT_EQ_TYPES: Final = [
    "PARAMETRIC",
    "LOWPASS_12",
    "HIGHPASS_12",
    "LOW_SHELF_Q",
    "HIGH_SHELF_Q",
]
INPUT_EQ_GAIN_MIN: Final = -15.0
INPUT_EQ_GAIN_MAX: Final = 15.0
EQ_FREQ_MIN: Final = 20.0
EQ_FREQ_MAX: Final = 20000.0
EQ_Q_MIN: Final = 0.4
EQ_Q_MAX: Final = 30.0

# --- Services -------------------------------------------------------------
SERVICE_SET_INPUT_EQ_BAND: Final = "set_input_eq_band"
SERVICE_SET_REGISTER: Final = "set_register"
