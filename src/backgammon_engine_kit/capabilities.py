"""Evidence-gated engine capability reporting."""

from dataclasses import dataclass

from .models import ANALYSIS_SETTINGS


CAPABILITY_SCHEMA_VERSION = "engine-capabilities-v1"


@dataclass(frozen=True)
class SettingCapability:
    analysis_setting: str
    checker: bool
    cube: bool
    evidence: str

    def to_dict(self):
        return {
            "analysis_setting": self.analysis_setting,
            "checker": self.checker,
            "cube": self.cube,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class EngineCapability:
    engine: str
    engine_version: object
    settings: tuple

    def __post_init__(self):
        object.__setattr__(self, "settings", tuple(self.settings))

    def supports(self, analysis_setting, decision_type):
        for setting in self.settings:
            if setting.analysis_setting == analysis_setting:
                return setting.checker if decision_type == "checker" else setting.cube
        return False

    def to_dict(self):
        return {
            "engine": self.engine,
            "engine_version": self.engine_version,
            "settings": [setting.to_dict() for setting in self.settings],
        }


@dataclass(frozen=True)
class CapabilityReport:
    engines: tuple
    schema_version: str = CAPABILITY_SCHEMA_VERSION

    def __post_init__(self):
        object.__setattr__(self, "engines", tuple(self.engines))

    def for_engine(self, name):
        return next(engine for engine in self.engines if engine.engine == name)

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "engines": [engine.to_dict() for engine in self.engines],
        }


def _unavailable(engine):
    if engine == "gnu":
        evidence = "unavailable: verified single-position checker and cube transcripts are required"
    else:
        evidence = "unavailable: verified normalized checker, cube, and rollout artifacts with configuration provenance are required"
    return EngineCapability(
        engine=engine,
        engine_version=None,
        settings=tuple(
            SettingCapability(setting, checker=False, cube=False, evidence=evidence)
            for setting in sorted(ANALYSIS_SETTINGS)
        ),
    )


def capability_report():
    gnu_settings = []
    for setting in sorted(ANALYSIS_SETTINGS):
        if setting == "1ply":
            gnu_settings.append(
                SettingCapability(
                    setting,
                    checker=True,
                    cube=True,
                    evidence="evidence/gnu/1.08.003/checker-1ply and cube-1ply",
                )
            )
        else:
            gnu_settings.append(
                SettingCapability(
                    setting,
                    checker=False,
                    cube=False,
                    evidence="unavailable: no verified GNU transcript for this setting",
                )
            )
    gnu = EngineCapability(
        engine="gnu",
        engine_version="1.08.003 20260710",
        settings=tuple(gnu_settings),
    )
    sage_settings = []
    for setting in sorted(ANALYSIS_SETTINGS):
        if setting == "1ply":
            sage_settings.append(
                SettingCapability(
                    setting,
                    checker=True,
                    cube=True,
                    evidence="evidence/sage/1.2.20260706/checker-1ply and cube-1ply",
                )
            )
        else:
            sage_settings.append(
                SettingCapability(
                    setting,
                    checker=False,
                    cube=False,
                    evidence="unavailable: no verified Sage artifact for this setting",
                )
            )
    sage = EngineCapability(
        engine="sage",
        engine_version="1.2.20260706",
        settings=tuple(sage_settings),
    )
    return CapabilityReport(engines=(sage, gnu))
