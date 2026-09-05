"""Wraps calls to the LLM backend used by the decision engine. Designed
so a local model or a remote API can be swapped in transparently."""

from typing import Dict, List, Optional


class LLMService:
    def __init__(self, provider: str = "local", api_key: Optional[str] = None):
        self.provider = provider
        self.api_key = api_key

    def complete(self, prompt: str, context: Optional[List[Dict]] = None) -> str:
        """Send a prompt (plus optional structured context) to the configured
        LLM provider and return the raw text response.

        TODO: implement provider-specific calls (local ONNX/GGUF model,
        or a remote API), always passing only sanitized context.
        """
        raise NotImplementedError("Configure an LLM provider before calling complete().")
