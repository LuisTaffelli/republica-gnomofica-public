from core.config import ProviderConfig, create_provider
from providers import OllamaProvider


def test_configuration_has_operational_defaults() -> None:
    # Toda configuración ausente conserva los valores predeterminados.
    config = ProviderConfig.from_env({})

    assert config == ProviderConfig()


def test_explicit_configuration_reaches_ollama_provider() -> None:
    # Opciones explícitas llegan al provider sin realizar una consulta.
    config = ProviderConfig.from_env(
        {
            "REPUBLICA_PROVIDER": "ollama",
            "REPUBLICA_MODEL": "modelo-de-prueba",
            "REPUBLICA_PROVIDER_ENDPOINT": "http://ollama.test:1234/",
            "REPUBLICA_PROVIDER_TIMEOUT": "12.5",
            "REPUBLICA_OLLAMA_THINKING": "true",
        }
    )

    provider = create_provider(config)

    assert isinstance(provider, OllamaProvider)
    assert provider.model == "modelo-de-prueba"
    assert provider.host == "http://ollama.test:1234"
    assert provider.timeout == 12.5
    assert provider.thinking is True


def test_ollama_thinking_defaults_off_and_rejects_ambiguous_values() -> None:
    assert ProviderConfig.from_env({}).ollama_thinking is False

    import pytest
    with pytest.raises(ValueError, match="debe ser true o false"):
        ProviderConfig.from_env({"REPUBLICA_OLLAMA_THINKING": "auto"})
