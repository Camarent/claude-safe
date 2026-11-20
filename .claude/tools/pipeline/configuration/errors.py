"""The one error class of the configuration loader."""


class ConfigError(ValueError):
    """pipeline.json is wrong in a way the loader must not hide."""
