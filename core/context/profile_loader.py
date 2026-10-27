from __future__ import annotations

from pathlib import Path
from typing import Any

import tomllib

from core.paths import PROFILES_DIR


class ProfileLoader:
    def __init__(
        self,
        profiles_dir: str | Path | None = None,
    ) -> None:
        self.profiles_dir = (
            Path(profiles_dir)
            if profiles_dir is not None
            else PROFILES_DIR
        )

    def load(self, profile_name: str) -> dict[str, Any]:
        clean_name = profile_name.strip()

        if not clean_name:
            raise ValueError(
                "El nombre del perfil no puede estar vacío."
            )

        profile_dir = (
            self.profiles_dir / clean_name
        ).resolve()
        profile_path = profile_dir / "profile.toml"

        if not profile_path.is_file():
            raise FileNotFoundError(
                f"No existe el profile: {profile_path}"
            )

        with profile_path.open("rb") as file:
            profile: dict[str, Any] = tomllib.load(file)

        self._validate_profile(
            profile=profile,
            profile_path=profile_path,
        )

        profile["profile_dir"] = profile_dir
        return profile

    @staticmethod
    def _validate_profile(
        *,
        profile: dict[str, Any],
        profile_path: Path,
    ) -> None:
        required = {
            "name",
            "identity",
            "prompt",
        }

        missing = sorted(
            key
            for key in required
            if not isinstance(profile.get(key), str)
            or not profile[key].strip()
        )

        if missing:
            raise ValueError(
                f"Perfil inválido en {profile_path}. "
                "Faltan campos: "
                + ", ".join(missing)
            )

        documents = profile.get("documents", [])

        if not isinstance(documents, list) or not all(
            isinstance(document, str)
            and document.strip()
            for document in documents
        ):
            raise ValueError(
                f"'documents' debe ser una lista de rutas "
                f"en {profile_path}."
            )
