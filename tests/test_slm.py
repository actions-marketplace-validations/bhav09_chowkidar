"""Tests for SLM prompts and response parsing (no Ollama required)."""


from pathlib import Path
from unittest.mock import patch
from chowkidar.slm.prompts import format_extraction_prompt, parse_slm_response


class TestPromptFormatting:
    def test_format_prompt(self):
        prompt = format_extraction_prompt("OpenAI is deprecating gpt-3.5-turbo on 2025-09-01")
        assert "gpt-3.5-turbo" in prompt
        assert "YYYY-MM-DD" in prompt

    def test_truncates_long_text(self):
        long_text = "x" * 10000
        prompt = format_extraction_prompt(long_text)
        assert "truncated" in prompt


class TestResponseParsing:
    def test_valid_response(self):
        response = """[
            {
                "model": "gpt-3.5-turbo",
                "provider": "openai",
                "sunset_date": "2025-09-01",
                "replacement": "gpt-4o-mini",
                "confidence": "high"
            }
        ]"""
        result = parse_slm_response(response)
        assert result is not None
        assert len(result) == 1
        assert result[0]["model"] == "gpt-3.5-turbo"
        assert result[0]["sunset_date"] == "2025-09-01"

    def test_invalid_json(self):
        result = parse_slm_response("This is not JSON")
        assert result is None

    def test_empty_array(self):
        result = parse_slm_response("[]")
        assert result is None

    def test_invalid_date(self):
        response = '[{"model": "gpt-4", "sunset_date": "not-a-date", "provider": "openai"}]'
        result = parse_slm_response(response)
        assert result is None  # Invalid date causes rejection

    def test_future_date_limit(self):
        response = '[{"model": "gpt-4", "sunset_date": "2035-01-01", "provider": "openai"}]'
        result = parse_slm_response(response)
        assert result is None  # Year > 2030 rejected

    def test_extracts_json_from_text(self):
        json_part = '[{"model": "gpt-4o", "provider": "openai", "sunset_date": "2026-01-01"}]'
        response = f"Here is the result:\n{json_part}\nDone."
        result = parse_slm_response(response)
        assert result is not None
        assert result[0]["model"] == "gpt-4o"

    def test_normalizes_confidence(self):
        response = '[{"model": "gpt-4", "provider": "openai", "sunset_date": "2025-06-01", "confidence": "INVALID"}]'
        result = parse_slm_response(response)
        assert result is not None
        assert result[0]["confidence"] == "low"


class TestFilesystemFallback:
    @patch("chowkidar.slm.setup.Path")
    def test_check_model_on_disk_found(self, mock_path_class):
        from unittest.mock import MagicMock
        from chowkidar.slm.setup import check_model_on_disk
        mock_file = MagicMock()
        mock_file.exists.return_value = True
        
        # Mocks Path.home() and the division operations on Path
        mock_path_class.home.return_value = MagicMock()
        mock_path_class.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value = mock_file
        
        # Mock Path.exists globally as well for safety
        with patch.object(Path, "exists", return_value=True):
            assert check_model_on_disk("gemma3:1b") is True
            
    def test_check_model_on_disk_not_found(self):
        from chowkidar.slm.setup import check_model_on_disk
        with patch.object(Path, "exists", return_value=False):
            assert check_model_on_disk("gemma3:1b") is False

    @patch("chowkidar.slm.setup.subprocess.run")
    @patch("chowkidar.slm.setup.check_model_on_disk")
    def test_check_model_available_falls_back_to_disk(self, mock_check_on_disk, mock_run):
        from chowkidar.slm.setup import check_model_available
        mock_run.side_effect = Exception("Ollama not running")
        mock_check_on_disk.return_value = True
        
        assert check_model_available("gemma3:1b") is True
        mock_check_on_disk.assert_called_once_with("gemma3:1b")

    def test_get_installed_ollama_models_filesystem_fallback(self):
        from unittest.mock import MagicMock
        from chowkidar.slm.selector import get_installed_ollama_models
        
        with patch("chowkidar.slm.client._get_ollama", return_value=None), \
             patch("chowkidar.slm.selector.subprocess.run", side_effect=Exception("No CLI")), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "iterdir") as mock_iterdir:
             
             mock_reg_dir = MagicMock()
             mock_reg_dir.is_dir.return_value = True
             mock_reg_dir.name = "registry.ollama.ai"
             
             mock_ns_dir = MagicMock()
             mock_ns_dir.is_dir.return_value = True
             mock_ns_dir.name = "library"
             
             mock_model_dir = MagicMock()
             mock_model_dir.is_dir.return_value = True
             mock_model_dir.name = "gemma3"
             
             mock_tag_file = MagicMock()
             mock_tag_file.is_file.return_value = True
             mock_tag_file.name = "1b"
             
             mock_reg_dir.iterdir.return_value = [mock_ns_dir]
             mock_ns_dir.iterdir.return_value = [mock_model_dir]
             mock_model_dir.iterdir.return_value = [mock_tag_file]
             
             mock_iterdir.return_value = [mock_reg_dir]
             
             models = get_installed_ollama_models()
             assert "gemma3:1b" in models

    @patch("chowkidar.slm.setup.check_ollama_installed", return_value=True)
    @patch("chowkidar.slm.setup.ensure_ollama_running", return_value=True)
    @patch("chowkidar.slm.setup.check_model_available", return_value=True)
    @patch("chowkidar.slm.setup.pull_model")
    @patch("chowkidar.slm.setup.Config")
    @patch("chowkidar.slm.setup.logger")
    def test_full_setup_skips_pull_when_global_model_exists(
        self, mock_logger, mock_config_class, mock_pull, mock_avail, mock_ensure, mock_installed
    ):
        from unittest.mock import MagicMock
        from chowkidar.slm.setup import full_setup
        
        mock_config = MagicMock()
        mock_config.get.return_value = "gemma3:1b"
        mock_config_class.return_value = mock_config
        
        with patch("chowkidar.slm.selector.select_best_slm", return_value=("gemma3:1b", "Mock reason")):
            success, msg = full_setup(skip_slm=False)
            
            assert success is True
            assert "ready" in msg
            mock_pull.assert_not_called()
            mock_logger.info.assert_any_call(
                "Model '%s' is already installed globally via Ollama. Skipping download.", "gemma3:1b"
            )


class TestSLMMetadataAndParsing:
    def test_parse_parameter_size_from_name(self):
        from chowkidar.slm.selector import parse_parameter_size_from_name
        assert parse_parameter_size_from_name("qwen2.5:0.5b") == 0.5
        assert parse_parameter_size_from_name("gemma3:1b") == 1.0
        assert parse_parameter_size_from_name("llama3:70B") == 70.0
        assert parse_parameter_size_from_name("invalid") is None

    @patch("chowkidar.slm.selector.subprocess.run")
    def test_get_model_metadata(self, mock_run):
        from unittest.mock import MagicMock
        from chowkidar.slm.selector import get_model_metadata
        
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = """
Model
  architecture        gemma4
  parameters          8.0B
  context length      131072

Capabilities
  completion
  vision

Parameters
  temperature    1
"""
        mock_run.return_value = mock_res
        
        meta = get_model_metadata("gemma4:e4b")
        assert meta["architecture"] == "gemma4"
        assert meta["parameters"] == "8.0B"
        assert meta["context_length"] == 131072
        assert "completion" in meta["capabilities"]
        assert "vision" in meta["capabilities"]

    @patch("chowkidar.slm.selector.get_model_size_from_manifest")
    @patch("chowkidar.slm.selector.get_model_metadata")
    def test_is_arbitrary_model_eligible(self, mock_meta, mock_size):
        from chowkidar.slm.selector import is_arbitrary_model_eligible
        
        # Scenario 1: fully eligible model
        mock_size.return_value = 4.5
        mock_meta.return_value = {
            "architecture": "llama",
            "context_length": 8192,
            "capabilities": ["completion"]
        }
        eligible, reason = is_arbitrary_model_eligible("llama3:8b", 16.0, 50.0)
        assert eligible is True
        
        # Scenario 2: too large size for RAM
        eligible, reason = is_arbitrary_model_eligible("llama3:8b", 4.0, 50.0)
        assert eligible is False
        assert "exceeds safe hardware limit" in reason
        
        # Scenario 3: embeddings-only model
        mock_size.return_value = 1.0
        mock_meta.return_value = {
            "architecture": "nomic-bert",
            "capabilities": []
        }
        eligible, reason = is_arbitrary_model_eligible("nomic-embed-text", 16.0, 50.0)
        assert eligible is False
        assert "embeddings" in reason
