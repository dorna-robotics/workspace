# workspace/components/factory.py

# Global registry: maps type string -> class
_registry: dict[str, type] = {}


def register(type_name: str):
    """
    Class decorator to register a component type.
    Backward compatible:
    - sets cls._registered_type = type_name
    - older classes (registered before change) still work without it
    """
    def decorator(cls):
        _registry[type_name] = cls

        # NEW behavior (safe):
        # attach the type to the class if not already set
        if not hasattr(cls, "_registered_type"):
            cls._registered_type = type_name

        return cls
    return decorator


def create_component(name: str, cfg: dict, workspace):
    """
    Factory function: creates a component from its config dict.
    Config dict must contain "type".
    """
    type_name = cfg.get("type")
    if not type_name:
        raise ValueError(f"Component '{name}' missing 'type' in config")

    cls = _registry.get(type_name)
    if cls is None:
        raise ValueError(f"Unknown component type '{type_name}' for '{name}'")

    return cls(name, cfg, workspace)
