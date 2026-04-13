"""Configuration management for payp.

Two-level config:
- Global: ~/.payp/ (user-level, never shared)
- Project: ./payp/ (git-tracked, team-shared)
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import tomli_w

from payp.models import (
    AppConfig,
    ConnectionCredential,
    ConnectionProfile,
    MemoryConfig,
    ModelProvider,
    ModelRoles,
)

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef,import-not-found]


GLOBAL_DIR = Path.home() / ".payp"
PROJECT_DIR_NAME = "payp"


def global_dir() -> Path:
    """Return the global payp config directory, creating it if needed."""
    d = GLOBAL_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_dir() -> Path | None:
    """Return the project-level payp directory if it exists."""
    d = Path.cwd() / PROJECT_DIR_NAME
    return d if d.is_dir() else None


def ensure_project_dir() -> Path:
    """Return the project-level payp directory, creating it if needed."""
    d = Path.cwd() / PROJECT_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_knowledge_dir() -> Path | None:
    """Return ./payp/knowledge if it exists, else None.

    Project-local knowledge takes precedence over global ~/.payp/knowledge/
    when both are present — same resolution order as connection profiles.
    """
    proj = project_dir()
    if proj is None:
        return None
    d = proj / "knowledge"
    return d if d.is_dir() else None


# --- App Config ---


def load_config() -> AppConfig:
    """Load global config from ~/.payp/config.toml."""
    path = global_dir() / "config.toml"
    if not path.exists():
        return AppConfig()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    roles_data = data.pop("roles", {})
    memory_data = data.pop("memory", {})
    config = AppConfig(**data, roles=ModelRoles(**roles_data), memory=MemoryConfig(**memory_data))
    return config


def save_config(config: AppConfig) -> None:
    """Save global config to ~/.payp/config.toml."""
    path = global_dir() / "config.toml"
    data = config.model_dump(mode="json", exclude_none=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


# --- Connection Profiles ---


def connections_dir() -> Path:
    """Return the global connections directory."""
    d = global_dir() / "connections"
    d.mkdir(exist_ok=True)
    return d


def list_connections() -> list[str]:
    """List all saved connection profile names."""
    d = connections_dir()
    return sorted({p.stem for p in d.glob("*.toml")})


def load_connection_profile(name: str) -> ConnectionProfile | None:
    """Load a connection profile by name.

    Resolution order: project-level → global.
    """
    # Check project-level first
    proj = project_dir()
    if proj:
        path = proj / "connections" / f"{name}.toml"
        if path.exists():
            with open(path, "rb") as f:
                data = tomllib.load(f)
            return ConnectionProfile(**data.get("connection", data))

    # Fall back to global
    path = connections_dir() / f"{name}.toml"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return ConnectionProfile(**data.get("connection", data))


def save_connection_profile(profile: ConnectionProfile) -> None:
    """Save a connection profile to global connections dir."""
    path = connections_dir() / f"{profile.name}.toml"
    data = {"connection": profile.model_dump(mode="json", exclude_none=True)}
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def load_credential(name: str) -> ConnectionCredential | None:
    """Load credentials for a connection (global only, never in project)."""
    path = connections_dir() / f"{name}.cred"
    if not path.exists():
        # Check env var override
        env_key = f"PAYP_{name.upper().replace('-', '_')}_PASSWORD"
        env_val = os.environ.get(env_key)
        if env_val:
            return ConnectionCredential(password=env_val)
        return None
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return ConnectionCredential(**data)


def save_credential(name: str, credential: ConnectionCredential) -> None:
    """Save credentials for a connection. Sets chmod 600."""
    path = connections_dir() / f"{name}.cred"
    data = credential.model_dump(mode="json", exclude_none=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    # Secure the file: owner read/write only
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def delete_connection(name: str) -> None:
    """Delete a connection profile and its credentials."""
    for suffix in (".toml", ".cred"):
        path = connections_dir() / f"{name}{suffix}"
        if path.exists():
            path.unlink()


# --- Model Providers ---


def load_models_config() -> dict[str, ModelProvider]:
    """Load model provider configurations from ~/.payp/models.toml."""
    path = global_dir() / "models.toml"
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)

    providers: dict[str, ModelProvider] = {}
    for key, val in data.items():
        if key == "roles":
            continue
        if isinstance(val, dict) and "api_key" in val:
            providers[key] = ModelProvider(**val)
    return providers


def load_model_roles() -> ModelRoles:
    """Load executor/reviewer role assignments."""
    path = global_dir() / "models.toml"
    if not path.exists():
        return ModelRoles()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    roles_data = data.get("roles", {})
    return ModelRoles(**roles_data)


def save_models_config(providers: dict[str, ModelProvider], roles: ModelRoles) -> None:
    """Save model provider configurations to ~/.payp/models.toml."""
    path = global_dir() / "models.toml"
    data: dict = {}
    for name, provider in providers.items():
        data[name] = provider.model_dump(mode="json", exclude_none=True)
    data["roles"] = roles.model_dump(mode="json", exclude_none=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    # Secure: contains API keys
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
