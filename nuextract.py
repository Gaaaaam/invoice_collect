import json
from typing import Any, Dict, List, Optional, Self
import os
import httpx

class NuExtractClient:
    def __init__(self, host, port, timeout: int = 30):
        self.base_url = f"http://{host}:{port}/api/v1"
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)
        self.default_template = {
            "ORGANIZATION": ["verbatim-string"],
            "ADDRESS": ["verbatim-string"],
            "PHONE": ["verbatim-string"],
            "EMAIL": ["verbatim-string"],
            "NAME": ["verbatim-string"],
        }

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def extract_from_files(
        self,
        file_paths: List[str],
        template: Optional[Dict[str, Any]] = None,
        examples: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Call NuExtract JSON interface by uploading files.

        Args:
            file_paths: The file path list
            template: The extraction template
            examples: The example data list (optional)
        Returns:
            Dict[str, Any]: The API response result
        """
        url = f"{self.base_url}/nuextract/"
        # url = self.base_url

        data = {
            "template": json.dumps(template or self.default_template, ensure_ascii=False),
        }

        if examples is not None:
            data["examples"] = json.dumps(examples, ensure_ascii=False)

        files = []
        try:
            for file_path in file_paths:
                files.append(("files", open(file_path, "rb")))

            response = await self.client.post(url, data=data, files=files)
            response.raise_for_status()
            return response.json()

        finally:
            for _, file_handle in files:
                if hasattr(file_handle, "close"):
                    file_handle.close()

    async def extract_from_base64(
        self,
        files_data: List[str],
        storage_filenames: List[str],
        template: Optional[Dict[str, Any]] = None,
        examples: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Call NuExtract JSON interface by uploading base64 data.

        Args:
            files_data: The list of base64 encoded file content
            storage_filenames: The list of corresponding file names
            template: The extraction template
            examples: The example data list (optional)
        Returns:
            Dict[str, Any]: The API response result
        """
        url = f"{self.base_url}/nuextract/json"

        files_payload = [{"value": b64_content} for b64_content in files_data]

        payload = {
            "files": files_payload,
            "template": template or self.default_template,
            "storage_filename": storage_filenames[0] if storage_filenames else "unknown",
        }

        if examples is not None:
            payload["examples"] = examples

        response = await self.client.post(url, json=payload)
        response.raise_for_status()
        return response.json()

    async def extract_from_text(
        self,
        text: str,
        template: Optional[Dict[str, Any]] = None,
        examples: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Call NuExtract JSON interface by uploading text.

        Args:
            text: The pure text content
            template: The extraction template
            examples: The example data list (optional)
        Returns:
            Dict[str, Any]: The API response result
        """
        url = f"{self.base_url}/nuextract/json"

        payload: Dict[str, Any] = {"text": text, "template": template or self.default_template}

        if examples is not None:
            payload["examples"] = examples

        response = await self.client.post(url, json=payload)
        response.raise_for_status()
        return response.json()
