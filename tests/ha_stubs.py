"""Minimal Home Assistant stand-ins for the wiring tests.

Home Assistant isn't installed in CI, so the tests that exercise the
integration's own modules (coordinator.py, sensor.py, entity.py, ...) load
the REAL files inside a synthetic package and substitute tiny stand-ins for
the handful of Home Assistant classes they touch. Everything else -- bus
decoding, water meter, load/save wiring, entity availability -- is the
shipped code. This checks the integration's own logic and glue, not Home
Assistant itself; it can't prove HA accepts the config flow, for example.

Usage:
    from ha_stubs import load
    Coordinator = load("coordinator").RainDirectorCoordinator
"""

import enum
import importlib
import os
import sys
import types

PKG_NAME = "rwh_pkg"
PKG_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "custom_components", "rainwater_harvesting")
)


# -- stand-in classes -------------------------------------------------------------

class FakeCoordinatorBase:
    """Stand-in for DataUpdateCoordinator."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, name=None, update_interval=None):
        self.hass = hass
        self.updates = []  # every snapshot pushed to entities

    def async_set_updated_data(self, data):
        self.data = data
        self.updates.append(data)


class FakeCoordinatorEntity:
    """Stand-in for CoordinatorEntity (base availability is simply True)."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return True


class FakeSensorEntity:
    """Stand-in for SensorEntity."""


class FakeStore:
    """Stand-in for helpers.storage.Store that records what it is asked to do."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, version, key):
        self.version, self.key = version, key
        self.loaded = None  # what async_load() will return
        self.saved = None  # last async_save() payload
        self.calls = []  # (data_func, delay) for each async_delay_save()

    async def async_load(self):
        return self.loaded

    async def async_save(self, data):
        self.saved = dict(data)

    def async_delay_save(self, data_func, delay=0):
        # Like the real Store, only remember the callback: it is called
        # when the delay timer fires, i.e. AFTER whatever scheduled the
        # save has finished updating its state -- not at scheduling time.
        self.calls.append((data_func, delay))

    @property
    def delayed(self):
        """What each scheduled save would write if its timer fired now."""
        return [(dict(func()), delay) for func, delay in self.calls]


class _Platform(enum.Enum):
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"


class _SensorDeviceClass(enum.Enum):
    WATER = "water"


class _SensorStateClass(enum.Enum):
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class _EntityCategory(enum.Enum):
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


class _UnitOfVolume(enum.Enum):
    LITERS = "L"


# -- installation -------------------------------------------------------------------

def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def install():
    """Install the stand-in homeassistant.* modules and the synthetic package."""
    if PKG_NAME in sys.modules:
        return
    _stub("homeassistant")
    _stub("homeassistant.core", HomeAssistant=object)
    _stub(
        "homeassistant.const",
        Platform=_Platform,
        PERCENTAGE="%",
        UnitOfVolume=_UnitOfVolume,
    )
    _stub("homeassistant.config_entries", ConfigEntry=object)
    _stub("homeassistant.components")
    _stub(
        "homeassistant.components.sensor",
        SensorDeviceClass=_SensorDeviceClass,
        SensorEntity=FakeSensorEntity,
        SensorStateClass=_SensorStateClass,
    )
    _stub("homeassistant.helpers")
    _stub("homeassistant.helpers.storage", Store=FakeStore)
    _stub(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=FakeCoordinatorBase,
        CoordinatorEntity=FakeCoordinatorEntity,
    )
    _stub("homeassistant.helpers.entity", DeviceInfo=dict, EntityCategory=_EntityCategory)
    _stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)

    # A synthetic package, so the relative imports resolve WITHOUT running
    # the real __init__.py (which needs far more of Home Assistant).
    pkg = types.ModuleType(PKG_NAME)
    pkg.__path__ = [PKG_DIR]
    sys.modules[PKG_NAME] = pkg


def load(module_name):
    """Import one of the integration's modules, e.g. load("coordinator")."""
    install()
    return importlib.import_module(f"{PKG_NAME}.{module_name}")
