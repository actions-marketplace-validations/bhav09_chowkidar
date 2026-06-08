"""Cloud environment adapter contracts.

Adapters are intentionally explicit: local deployment signals never grant
permission to mutate remote environments or secret stores.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol

import httpx

from .config import Config


@dataclass
class CloudTarget:
    adapter: str
    name: str
    metadata: dict = field(default_factory=dict)


@dataclass
class CloudOperationResult:
    status: str
    adapter: str
    target: str
    message: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class CloudEnvAdapter(Protocol):
    name: str

    def discover(self) -> list[CloudTarget]:
        ...

    def dry_run(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        ...

    def write(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        ...

    def verify(self, target: CloudTarget, variable_name: str, expected_value: str) -> CloudOperationResult:
        ...


class DisabledCloudAdapter:
    """Base adapter that documents unsupported writes until credentials are configured."""

    def __init__(self, name: str, config: Config | None = None) -> None:
        self.name = name
        self.config = config or Config()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get(f"cloud_{self.name}_enabled", False))

    def discover(self) -> list[CloudTarget]:
        if not self.enabled:
            return []
        return [CloudTarget(self.name, "configured-target", {"confirmed": False})]

    def dry_run(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        return self._blocked(target, "dry_run")

    def write(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        return self._blocked(target, "write")

    def verify(self, target: CloudTarget, variable_name: str, expected_value: str) -> CloudOperationResult:
        return self._blocked(target, "verify")

    def _blocked(self, target: CloudTarget, operation: str) -> CloudOperationResult:
        return CloudOperationResult(
            status="blocked",
            adapter=self.name,
            target=target.name,
            message=(
                f"{self.name} {operation} requires a concrete provider client, "
                "explicit credentials, dry-run mapping, and verification support."
            ),
            metadata={"operation": operation},
        )


class AWSSecretsAdapter:
    def __init__(self, config: Config | None = None) -> None:
        self.name = "aws"
        self.config = config or Config()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get(f"cloud_{self.name}_enabled", False))

    def discover(self) -> list[CloudTarget]:
        if not self.enabled:
            return []
        try:
            import boto3
        except ImportError:
            return []
        
        targets = []
        try:
            client = boto3.client("secretsmanager")
            response = client.list_secrets()
            for secret in response.get("SecretList", []):
                targets.append(CloudTarget(self.name, secret["Name"], {"type": "secretsmanager"}))
        except Exception:
            pass

        try:
            ssm = boto3.client("ssm")
            response = ssm.describe_parameters()
            for param in response.get("Parameters", []):
                targets.append(CloudTarget(self.name, param["Name"], {"type": "ssm"}))
        except Exception:
            pass

        return targets

    def dry_run(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled:
            return self._blocked(target, "dry_run")
        try:
            import boto3
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="boto3 library is required to run AWS dry-run.",
            )
        target_type = target.metadata.get("type", "secretsmanager")
        return CloudOperationResult(
            status="success",
            adapter=self.name,
            target=target.name,
            message=f"Dry run: Would update variable '{variable_name}' to '{new_value}' in AWS {target_type} '{target.name}'.",
            metadata={"variable": variable_name, "value": new_value, "type": target_type},
        )

    def write(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled:
            return self._blocked(target, "write")
        try:
            import boto3
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="boto3 library is required to write to AWS.",
            )

        target_type = target.metadata.get("type", "secretsmanager")
        try:
            if target_type == "secretsmanager":
                client = boto3.client("secretsmanager")
                try:
                    current = client.get_secret_value(SecretId=target.name)
                    secret_string = current.get("SecretString", "{}")
                    import json
                    secret_dict = json.loads(secret_string)
                except Exception:
                    secret_dict = {}
                secret_dict[variable_name] = new_value
                client.put_secret_value(SecretId=target.name, SecretString=json.dumps(secret_dict))
            else:
                ssm = boto3.client("ssm")
                ssm.put_parameter(Name=target.name, Value=new_value, Type="SecureString", Overwrite=True)

            return CloudOperationResult(
                status="success",
                adapter=self.name,
                target=target.name,
                message=f"Successfully updated variable '{variable_name}' in AWS {target_type} '{target.name}'.",
                metadata={"variable": variable_name, "type": target_type},
            )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to write to AWS {target_type}: {str(e)}",
            )

    def verify(self, target: CloudTarget, variable_name: str, expected_value: str) -> CloudOperationResult:
        if not self.enabled:
            return self._blocked(target, "verify")
        try:
            import boto3
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="boto3 library is required to verify AWS values.",
            )

        target_type = target.metadata.get("type", "secretsmanager")
        try:
            actual_value = None
            if target_type == "secretsmanager":
                client = boto3.client("secretsmanager")
                current = client.get_secret_value(SecretId=target.name)
                secret_string = current.get("SecretString", "{}")
                import json
                secret_dict = json.loads(secret_string)
                actual_value = secret_dict.get(variable_name)
            else:
                ssm = boto3.client("ssm")
                param = ssm.get_parameter(Name=target.name, WithDecryption=True)
                actual_value = param.get("Parameter", {}).get("Value")

            if actual_value == expected_value:
                return CloudOperationResult(
                    status="success",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification passed: '{variable_name}' is set to expected value in AWS {target_type} '{target.name}'.",
                )
            else:
                return CloudOperationResult(
                    status="failed",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification failed: expected '{expected_value}', got '{actual_value}' in AWS {target_type} '{target.name}'.",
                )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to verify AWS {target_type}: {str(e)}",
            )

    def _blocked(self, target: CloudTarget, operation: str) -> CloudOperationResult:
        return CloudOperationResult(
            status="blocked",
            adapter=self.name,
            target=target.name,
            message=(
                f"{self.name} {operation} requires enabling cloud_{self.name}_enabled in configuration."
            ),
            metadata={"operation": operation},
        )


class GCPSecretManagerAdapter:
    def __init__(self, config: Config | None = None) -> None:
        self.name = "gcp"
        self.config = config or Config()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get(f"cloud_{self.name}_enabled", False))

    @property
    def project_id(self) -> str:
        return str(self.config.get("cloud_gcp_project_id", ""))

    def discover(self) -> list[CloudTarget]:
        if not self.enabled or not self.project_id:
            return []
        try:
            from google.cloud import secretmanager
        except ImportError:
            return []

        targets = []
        try:
            client = secretmanager.SecretManagerServiceClient()
            parent = f"projects/{self.project_id}"
            for secret in client.list_secrets(request={"parent": parent}):
                secret_name = secret.name.split("/")[-1]
                targets.append(CloudTarget(self.name, secret_name))
        except Exception:
            pass
        return targets

    def dry_run(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled or not self.project_id:
            return self._blocked(target, "dry_run")
        try:
            from google.cloud import secretmanager
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="google-cloud-secret-manager library is required to run GCP dry-run.",
            )
        return CloudOperationResult(
            status="success",
            adapter=self.name,
            target=target.name,
            message=f"Dry run: Would update variable '{variable_name}' to '{new_value}' in GCP Secret Manager '{target.name}'.",
            metadata={"variable": variable_name, "value": new_value},
        )

    def write(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled or not self.project_id:
            return self._blocked(target, "write")
        try:
            from google.cloud import secretmanager
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="google-cloud-secret-manager library is required to write to GCP.",
            )

        try:
            client = secretmanager.SecretManagerServiceClient()
            parent = f"projects/{self.project_id}/secrets/{target.name}"
            
            try:
                latest = f"{parent}/versions/latest"
                response = client.access_secret_version(request={"name": latest})
                secret_string = response.payload.data.decode("UTF-8")
                import json
                secret_dict = json.loads(secret_string)
            except Exception:
                secret_dict = {}

            secret_dict[variable_name] = new_value
            payload_bytes = json.dumps(secret_dict).encode("UTF-8")
            client.add_secret_version(
                request={"parent": parent, "payload": {"data": payload_bytes}}
            )

            return CloudOperationResult(
                status="success",
                adapter=self.name,
                target=target.name,
                message=f"Successfully updated variable '{variable_name}' in GCP Secret Manager '{target.name}'.",
                metadata={"variable": variable_name},
            )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to write to GCP Secret Manager: {str(e)}",
            )

    def verify(self, target: CloudTarget, variable_name: str, expected_value: str) -> CloudOperationResult:
        if not self.enabled or not self.project_id:
            return self._blocked(target, "verify")
        try:
            from google.cloud import secretmanager
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="google-cloud-secret-manager library is required to verify GCP values.",
            )

        try:
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{self.project_id}/secrets/{target.name}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            secret_string = response.payload.data.decode("UTF-8")
            
            import json
            try:
                secret_dict = json.loads(secret_string)
                actual_value = secret_dict.get(variable_name)
            except Exception:
                actual_value = secret_string

            if actual_value == expected_value:
                return CloudOperationResult(
                    status="success",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification passed: '{variable_name}' is set to expected value in GCP Secret Manager '{target.name}'.",
                )
            else:
                return CloudOperationResult(
                    status="failed",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification failed: expected '{expected_value}', got '{actual_value}' in GCP Secret Manager '{target.name}'.",
                )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to verify GCP Secret Manager: {str(e)}",
            )

    def _blocked(self, target: CloudTarget, operation: str) -> CloudOperationResult:
        return CloudOperationResult(
            status="blocked",
            adapter=self.name,
            target=target.name,
            message=(
                f"{self.name} {operation} requires enabling cloud_{self.name}_enabled and setting cloud_gcp_project_id in configuration."
            ),
            metadata={"operation": operation},
        )


class AzureKeyVaultAdapter:
    def __init__(self, config: Config | None = None) -> None:
        self.name = "azure"
        self.config = config or Config()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get(f"cloud_{self.name}_enabled", False))

    @property
    def vault_url(self) -> str:
        return str(self.config.get("cloud_azure_vault_url", ""))

    def discover(self) -> list[CloudTarget]:
        if not self.enabled or not self.vault_url:
            return []
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError:
            return []

        targets = []
        try:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=self.vault_url, credential=credential)
            for secret_properties in client.list_properties_of_secrets():
                targets.append(CloudTarget(self.name, secret_properties.name))
        except Exception:
            pass
        return targets

    def dry_run(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled or not self.vault_url:
            return self._blocked(target, "dry_run")
        try:
            from azure.keyvault.secrets import SecretClient
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="azure-keyvault-secrets library is required to run Azure dry-run.",
            )
        return CloudOperationResult(
            status="success",
            adapter=self.name,
            target=target.name,
            message=f"Dry run: Would update variable '{variable_name}' to '{new_value}' in Azure Key Vault '{target.name}'.",
            metadata={"variable": variable_name, "value": new_value},
        )

    def write(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled or not self.vault_url:
            return self._blocked(target, "write")
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="azure-keyvault-secrets and azure-identity libraries are required to write to Azure.",
            )

        try:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=self.vault_url, credential=credential)
            
            try:
                current = client.get_secret(target.name)
                secret_string = current.value or "{}"
                import json
                secret_dict = json.loads(secret_string)
                secret_dict[variable_name] = new_value
                new_secret_value = json.dumps(secret_dict)
            except Exception:
                new_secret_value = new_value

            client.set_secret(target.name, new_secret_value)
            return CloudOperationResult(
                status="success",
                adapter=self.name,
                target=target.name,
                message=f"Successfully updated variable '{variable_name}' in Azure Key Vault '{target.name}'.",
                metadata={"variable": variable_name},
            )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to write to Azure Key Vault: {str(e)}",
            )

    def verify(self, target: CloudTarget, variable_name: str, expected_value: str) -> CloudOperationResult:
        if not self.enabled or not self.vault_url:
            return self._blocked(target, "verify")
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="azure-keyvault-secrets and azure-identity libraries are required to verify Azure values.",
            )

        try:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=self.vault_url, credential=credential)
            secret = client.get_secret(target.name)
            secret_string = secret.value or ""
            
            import json
            try:
                secret_dict = json.loads(secret_string)
                actual_value = secret_dict.get(variable_name)
            except Exception:
                actual_value = secret_string

            if actual_value == expected_value:
                return CloudOperationResult(
                    status="success",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification passed: '{variable_name}' is set to expected value in Azure Key Vault '{target.name}'.",
                )
            else:
                return CloudOperationResult(
                    status="failed",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification failed: expected '{expected_value}', got '{actual_value}' in Azure Key Vault '{target.name}'.",
                )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to verify Azure Key Vault: {str(e)}",
            )

    def _blocked(self, target: CloudTarget, operation: str) -> CloudOperationResult:
        return CloudOperationResult(
            status="blocked",
            adapter=self.name,
            target=target.name,
            message=(
                f"{self.name} {operation} requires enabling cloud_{self.name}_enabled and setting cloud_azure_vault_url in configuration."
            ),
            metadata={"operation": operation},
        )


class VercelAdapter:
    def __init__(self, config: Config | None = None) -> None:
        self.name = "vercel"
        self.config = config or Config()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get(f"cloud_{self.name}_enabled", False))

    @property
    def token(self) -> str:
        return str(self.config.get("cloud_vercel_token", ""))

    @property
    def project_id(self) -> str:
        return str(self.config.get("cloud_vercel_project_id", ""))

    @property
    def team_id(self) -> str:
        return str(self.config.get("cloud_vercel_team_id", ""))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _params(self) -> dict[str, str]:
        p = {}
        if self.team_id:
            p["teamId"] = self.team_id
        return p

    def discover(self) -> list[CloudTarget]:
        if not self.enabled or not self.token or not self.project_id:
            return []
        targets = []
        try:
            url = f"https://api.vercel.com/v9/projects/{self.project_id}/env"
            resp = httpx.get(url, headers=self._headers(), params=self._params(), timeout=10)
            if resp.status_code == 200:
                envs = resp.json().get("envs", [])
                for env in envs:
                    targets.append(CloudTarget(self.name, env["key"], {"id": env["id"]}))
        except Exception:
            pass
        return targets

    def dry_run(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled or not self.token or not self.project_id:
            return self._blocked(target, "dry_run")
        return CloudOperationResult(
            status="success",
            adapter=self.name,
            target=target.name,
            message=f"Dry run: Would update environment variable '{variable_name}' to '{new_value}' in Vercel project '{self.project_id}'.",
            metadata={"variable": variable_name, "value": new_value},
        )

    def write(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled or not self.token or not self.project_id:
            return self._blocked(target, "write")
        try:
            url = f"https://api.vercel.com/v9/projects/{self.project_id}/env"
            resp = httpx.get(url, headers=self._headers(), params=self._params(), timeout=10)
            env_id = None
            if resp.status_code == 200:
                envs = resp.json().get("envs", [])
                for env in envs:
                    if env["key"] == variable_name:
                        env_id = env["id"]
                        break

            if env_id:
                update_url = f"https://api.vercel.com/v9/projects/{self.project_id}/env/{env_id}"
                payload = {"value": new_value}
                resp = httpx.patch(update_url, headers=self._headers(), params=self._params(), json=payload, timeout=10)
            else:
                payload = {
                    "key": variable_name,
                    "value": new_value,
                    "type": "secret",
                    "target": ["production", "preview", "development"],
                }
                resp = httpx.post(url, headers=self._headers(), params=self._params(), json=payload, timeout=10)

            if 200 <= resp.status_code < 300:
                return CloudOperationResult(
                    status="success",
                    adapter=self.name,
                    target=target.name,
                    message=f"Successfully updated variable '{variable_name}' in Vercel project '{self.project_id}'.",
                )
            else:
                return CloudOperationResult(
                    status="failed",
                    adapter=self.name,
                    target=target.name,
                    message=f"Failed to write to Vercel API: {resp.text}",
                )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to write to Vercel: {str(e)}",
            )

    def verify(self, target: CloudTarget, variable_name: str, expected_value: str) -> CloudOperationResult:
        if not self.enabled or not self.token or not self.project_id:
            return self._blocked(target, "verify")
        try:
            url = f"https://api.vercel.com/v9/projects/{self.project_id}/env"
            resp = httpx.get(url, headers=self._headers(), params=self._params(), timeout=10)
            actual_value = None
            if resp.status_code == 200:
                envs = resp.json().get("envs", [])
                for env in envs:
                    if env["key"] == variable_name:
                        actual_value = env.get("value")
                        break

            if actual_value == expected_value:
                return CloudOperationResult(
                    status="success",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification passed: '{variable_name}' is set to expected value in Vercel project '{self.project_id}'.",
                )
            else:
                return CloudOperationResult(
                    status="failed",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification failed: expected '{expected_value}', got '{actual_value}' in Vercel project '{self.project_id}'.",
                )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to verify Vercel: {str(e)}",
            )

    def _blocked(self, target: CloudTarget, operation: str) -> CloudOperationResult:
        return CloudOperationResult(
            status="blocked",
            adapter=self.name,
            target=target.name,
            message=(
                f"{self.name} {operation} requires a concrete provider client, "
                "explicit credentials, and enabling cloud_{self.name}_enabled, "
                "cloud_vercel_token, and cloud_vercel_project_id in configuration."
            ),
            metadata={"operation": operation},
        )


class KubernetesAdapter:
    def __init__(self, config: Config | None = None) -> None:
        self.name = "kubernetes"
        self.config = config or Config()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get(f"cloud_{self.name}_enabled", False))

    @property
    def namespace(self) -> str:
        return str(self.config.get("cloud_kubernetes_namespace", "default"))

    def _load_config(self) -> None:
        from kubernetes import config
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()

    def discover(self) -> list[CloudTarget]:
        if not self.enabled:
            return []
        try:
            from kubernetes import client
            self._load_config()
        except Exception:
            return []

        targets = []
        try:
            v1 = client.CoreV1Api()
            secrets = v1.list_namespaced_secret(namespace=self.namespace)
            for secret in secrets.items:
                targets.append(CloudTarget(self.name, secret.metadata.name, {"type": "secret"}))
            
            configmaps = v1.list_namespaced_config_map(namespace=self.namespace)
            for cm in configmaps.items:
                targets.append(CloudTarget(self.name, cm.metadata.name, {"type": "configmap"}))
        except Exception:
            pass
        return targets

    def dry_run(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled:
            return self._blocked(target, "dry_run")
        try:
            from kubernetes import client
        except ImportError:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message="kubernetes library is required to run Kubernetes dry-run.",
            )
        target_type = target.metadata.get("type", "secret")
        return CloudOperationResult(
            status="success",
            adapter=self.name,
            target=target.name,
            message=f"Dry run: Would update variable '{variable_name}' to '{new_value}' in Kubernetes {target_type} '{target.name}' in namespace '{self.namespace}'.",
            metadata={"variable": variable_name, "value": new_value, "type": target_type},
        )

    def write(self, target: CloudTarget, variable_name: str, new_value: str) -> CloudOperationResult:
        if not self.enabled:
            return self._blocked(target, "write")
        try:
            from kubernetes import client
            self._load_config()
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"kubernetes library is required and must be authenticated: {str(e)}",
            )

        target_type = target.metadata.get("type", "secret")
        try:
            v1 = client.CoreV1Api()
            if target_type == "secret":
                secret = v1.read_namespaced_secret(name=target.name, namespace=self.namespace)
                if secret.data is None:
                    secret.data = {}
                import base64
                encoded_value = base64.b64encode(new_value.encode("utf-8")).decode("utf-8")
                secret.data[variable_name] = encoded_value
                v1.replace_namespaced_secret(name=target.name, namespace=self.namespace, body=secret)
            else:
                cm = v1.read_namespaced_config_map(name=target.name, namespace=self.namespace)
                if cm.data is None:
                    cm.data = {}
                cm.data[variable_name] = new_value
                v1.replace_namespaced_config_map(name=target.name, namespace=self.namespace, body=cm)

            return CloudOperationResult(
                status="success",
                adapter=self.name,
                target=target.name,
                message=f"Successfully updated variable '{variable_name}' in Kubernetes {target_type} '{target.name}'.",
                metadata={"variable": variable_name, "type": target_type},
            )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to write to Kubernetes {target_type}: {str(e)}",
            )

    def verify(self, target: CloudTarget, variable_name: str, expected_value: str) -> CloudOperationResult:
        if not self.enabled:
            return self._blocked(target, "verify")
        try:
            from kubernetes import client
            self._load_config()
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"kubernetes library is required and must be authenticated: {str(e)}",
            )

        target_type = target.metadata.get("type", "secret")
        try:
            v1 = client.CoreV1Api()
            actual_value = None
            if target_type == "secret":
                secret = v1.read_namespaced_secret(name=target.name, namespace=self.namespace)
                if secret.data and variable_name in secret.data:
                    import base64
                    actual_value = base64.b64decode(secret.data[variable_name]).decode("utf-8")
            else:
                cm = v1.read_namespaced_config_map(name=target.name, namespace=self.namespace)
                if cm.data and variable_name in cm.data:
                    actual_value = cm.data[variable_name]

            if actual_value == expected_value:
                return CloudOperationResult(
                    status="success",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification passed: '{variable_name}' is set to expected value in Kubernetes {target_type} '{target.name}'.",
                )
            else:
                return CloudOperationResult(
                    status="failed",
                    adapter=self.name,
                    target=target.name,
                    message=f"Verification failed: expected '{expected_value}', got '{actual_value}' in Kubernetes {target_type} '{target.name}'.",
                )
        except Exception as e:
            return CloudOperationResult(
                status="failed",
                adapter=self.name,
                target=target.name,
                message=f"Failed to verify Kubernetes {target_type}: {str(e)}",
            )

    def _blocked(self, target: CloudTarget, operation: str) -> CloudOperationResult:
        return CloudOperationResult(
            status="blocked",
            adapter=self.name,
            target=target.name,
            message=(
                f"{self.name} {operation} requires enabling cloud_{self.name}_enabled in configuration."
            ),
            metadata={"operation": operation},
        )


def get_cloud_adapters(config: Config | None = None) -> list[CloudEnvAdapter]:
    cfg = config or Config()
    return [
        VercelAdapter(cfg),
        KubernetesAdapter(cfg),
        AWSSecretsAdapter(cfg),
        GCPSecretManagerAdapter(cfg),
        AzureKeyVaultAdapter(cfg),
    ]
