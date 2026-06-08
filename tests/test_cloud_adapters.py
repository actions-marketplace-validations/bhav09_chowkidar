"""Tests for concrete cloud environment adapters."""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock all cloud SDK modules in sys.modules to prevent ImportError
mock_boto3_mod = MagicMock()
mock_google_mod = MagicMock()
mock_azure_identity_mod = MagicMock()
mock_azure_keyvault_mod = MagicMock()
mock_kubernetes_mod = MagicMock()

sys.modules["boto3"] = mock_boto3_mod
sys.modules["google"] = mock_google_mod
sys.modules["google.cloud"] = mock_google_mod
sys.modules["google.cloud.secretmanager"] = mock_google_mod
sys.modules["azure"] = mock_azure_identity_mod
sys.modules["azure.identity"] = mock_azure_identity_mod
sys.modules["azure.keyvault"] = mock_azure_keyvault_mod
sys.modules["azure.keyvault.secrets"] = mock_azure_keyvault_mod
sys.modules["kubernetes"] = mock_kubernetes_mod
sys.modules["kubernetes.client"] = mock_kubernetes_mod
sys.modules["kubernetes.config"] = mock_kubernetes_mod

from chowkidar.cloud_adapters import (
    AWSSecretsAdapter,
    AzureKeyVaultAdapter,
    CloudTarget,
    GCPSecretManagerAdapter,
    KubernetesAdapter,
    VercelAdapter,
)
from chowkidar.config import Config


@pytest.fixture(autouse=True)
def reset_mocks():
    mock_boto3_mod.reset_mock()
    mock_google_mod.reset_mock()
    mock_azure_identity_mod.reset_mock()
    mock_azure_keyvault_mod.reset_mock()
    mock_kubernetes_mod.reset_mock()


@pytest.fixture
def mock_boto3_client():
    # Mock Secrets Manager Client
    mock_client = MagicMock()
    mock_client.list_secrets.return_value = {
        "SecretList": [{"Name": "my-aws-secret"}]
    }
    mock_client.get_secret_value.return_value = {
        "SecretString": '{"OPENAI_MODEL": "gpt-4o"}'
    }
    
    # Mock SSM Client
    mock_ssm = MagicMock()
    mock_ssm.describe_parameters.return_value = {
        "Parameters": [{"Name": "my-aws-param"}]
    }
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "gpt-3.5-turbo"}
    }

    def get_client(service_name, *args, **kwargs):
        if service_name == "secretsmanager":
            return mock_client
        return mock_ssm

    mock_boto3_mod.client.side_effect = get_client
    yield mock_client, mock_ssm


def test_aws_adapter_discover(mock_boto3_client):
    config = Config()
    config.set("cloud_aws_enabled", True)
    adapter = AWSSecretsAdapter(config)

    targets = adapter.discover()
    assert len(targets) == 2
    assert any(t.name == "my-aws-secret" and t.metadata["type"] == "secretsmanager" for t in targets)
    assert any(t.name == "my-aws-param" and t.metadata["type"] == "ssm" for t in targets)


def test_aws_adapter_write_secretsmanager(mock_boto3_client):
    mock_client, _ = mock_boto3_client
    config = Config()
    config.set("cloud_aws_enabled", True)
    adapter = AWSSecretsAdapter(config)

    target = CloudTarget("aws", "my-aws-secret", {"type": "secretsmanager"})
    result = adapter.write(target, "OPENAI_MODEL", "gpt-4o-mini")

    assert result.status == "success"
    mock_client.put_secret_value.assert_called_once()


def test_aws_adapter_verify_ssm(mock_boto3_client):
    _, mock_ssm = mock_boto3_client
    config = Config()
    config.set("cloud_aws_enabled", True)
    adapter = AWSSecretsAdapter(config)

    target = CloudTarget("aws", "my-aws-param", {"type": "ssm"})
    result = adapter.verify(target, "OPENAI_MODEL", "gpt-3.5-turbo")

    assert result.status == "success"
    mock_ssm.get_parameter.assert_called_once_with(Name="my-aws-param", WithDecryption=True)


@pytest.fixture
def mock_gcp_client():
    mock_client = MagicMock()
    # Correct mock path based on: from google.cloud import secretmanager
    mock_google_mod.secretmanager.SecretManagerServiceClient.return_value = mock_client
    
    # Mock list_secrets
    mock_secret = MagicMock()
    mock_secret.name = "projects/my-project/secrets/my-gcp-secret"
    mock_client.list_secrets.return_value = [mock_secret]
    
    # Mock access_secret_version
    mock_version_response = MagicMock()
    mock_version_response.payload.data = b'{"OPENAI_MODEL": "gpt-4"}'
    mock_client.access_secret_version.return_value = mock_version_response
    
    yield mock_client


def test_gcp_adapter_discover(mock_gcp_client):
    config = Config()
    config.set("cloud_gcp_enabled", True)
    config.set("cloud_gcp_project_id", "my-project")
    adapter = GCPSecretManagerAdapter(config)
    targets = adapter.discover()
    assert len(targets) == 1
    assert targets[0].name == "my-gcp-secret"


def test_gcp_adapter_write(mock_gcp_client):
    config = Config()
    config.set("cloud_gcp_enabled", True)
    config.set("cloud_gcp_project_id", "my-project")
    adapter = GCPSecretManagerAdapter(config)
    target = CloudTarget("gcp", "my-gcp-secret")
    result = adapter.write(target, "OPENAI_MODEL", "gpt-4o")

    assert result.status == "success"
    mock_gcp_client.add_secret_version.assert_called_once()


@pytest.fixture
def mock_azure_client():
    mock_client = MagicMock()
    # Correct mock path based on: from azure.keyvault.secrets import SecretClient
    mock_azure_keyvault_mod.SecretClient.return_value = mock_client
    
    # Mock list_properties_of_secrets
    mock_prop = MagicMock()
    mock_prop.name = "my-azure-secret"
    mock_client.list_properties_of_secrets.return_value = [mock_prop]
    
    # Mock get_secret
    mock_secret = MagicMock()
    mock_secret.value = '{"OPENAI_MODEL": "gpt-4"}'
    mock_client.get_secret.return_value = mock_secret
    
    yield mock_client


def test_azure_adapter_discover(mock_azure_client):
    config = Config()
    config.set("cloud_azure_enabled", True)
    config.set("cloud_azure_vault_url", "https://myvault.vault.azure.net/")
    adapter = AzureKeyVaultAdapter(config)

    targets = adapter.discover()
    assert len(targets) == 1
    assert targets[0].name == "my-azure-secret"


def test_azure_adapter_write(mock_azure_client):
    config = Config()
    config.set("cloud_azure_enabled", True)
    config.set("cloud_azure_vault_url", "https://myvault.vault.azure.net/")
    adapter = AzureKeyVaultAdapter(config)

    target = CloudTarget("azure", "my-azure-secret")
    result = adapter.write(target, "OPENAI_MODEL", "gpt-4o")

    assert result.status == "success"
    mock_azure_client.set_secret.assert_called_once()


@pytest.fixture
def mock_vercel_api():
    with patch("chowkidar.cloud_adapters.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "envs": [{"id": "env_1", "key": "OPENAI_MODEL", "value": "gpt-3.5-turbo"}]
        }
        mock_httpx.get.return_value = mock_resp
        mock_httpx.patch.return_value = mock_resp
        mock_httpx.post.return_value = mock_resp
        yield mock_httpx


def test_vercel_adapter_discover(mock_vercel_api):
    config = Config()
    config.set("cloud_vercel_enabled", True)
    config.set("cloud_vercel_token", "my-token")
    config.set("cloud_vercel_project_id", "my-project")
    adapter = VercelAdapter(config)

    targets = adapter.discover()
    assert len(targets) == 1
    assert targets[0].name == "OPENAI_MODEL"


def test_vercel_adapter_write(mock_vercel_api):
    config = Config()
    config.set("cloud_vercel_enabled", True)
    config.set("cloud_vercel_token", "my-token")
    config.set("cloud_vercel_project_id", "my-project")
    adapter = VercelAdapter(config)

    target = CloudTarget("vercel", "OPENAI_MODEL")
    result = adapter.write(target, "OPENAI_MODEL", "gpt-4o")

    assert result.status == "success"
    mock_vercel_api.patch.assert_called_once()


@pytest.fixture
def mock_k8s_client():
    mock_api = MagicMock()
    # Correct mock path based on: from kubernetes import client
    mock_kubernetes_mod.client.CoreV1Api.return_value = mock_api
    
    # Mock secrets
    mock_secret = MagicMock()
    mock_secret.metadata.name = "my-k8s-secret"
    mock_secret.data = {"OPENAI_MODEL": "Z3B0LTQuMA=="} # base64 for gpt-4.0
    
    mock_secret_list = MagicMock()
    mock_secret_list.items = [mock_secret]
    mock_api.list_namespaced_secret.return_value = mock_secret_list
    mock_api.read_namespaced_secret.return_value = mock_secret
    
    # Mock configmaps
    mock_cm = MagicMock()
    mock_cm.metadata.name = "my-k8s-cm"
    mock_cm.data = {"OPENAI_MODEL": "gpt-3.5-turbo"}
    
    mock_cm_list = MagicMock()
    mock_cm_list.items = [mock_cm]
    mock_api.list_namespaced_config_map.return_value = mock_cm_list
    mock_api.read_namespaced_config_map.return_value = mock_cm
    
    yield mock_api


def test_kubernetes_adapter_discover(mock_k8s_client):
    config = Config()
    config.set("cloud_kubernetes_enabled", True)
    adapter = KubernetesAdapter(config)

    targets = adapter.discover()
    assert len(targets) == 2
    assert any(t.name == "my-k8s-secret" and t.metadata["type"] == "secret" for t in targets)
    assert any(t.name == "my-k8s-cm" and t.metadata["type"] == "configmap" for t in targets)


def test_kubernetes_adapter_verify_secret(mock_k8s_client):
    config = Config()
    config.set("cloud_kubernetes_enabled", True)
    adapter = KubernetesAdapter(config)

    target = CloudTarget("kubernetes", "my-k8s-secret", {"type": "secret"})
    result = adapter.verify(target, "OPENAI_MODEL", "gpt-4.0")

    assert result.status == "success"
