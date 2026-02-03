"""VLM (Visual Language Model) engines for video analysis.

This module provides abstract and concrete implementations for VLM engines
that can analyze video frames and extract semantic information.

Supported backends:
- OpenAI GPT-4 Vision (GPT-4V)
- Google Gemini
- Local models (LLaVA and compatible)
"""

from __future__ import annotations

import base64
import io
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable, AsyncIterator

import httpx
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class VLMResponse:
    """Standardized response from VLM analysis.
    
    This structure normalizes responses from different VLM backends
    into a consistent format for downstream processing.
    """
    
    raw_response: Dict[str, Any]
    parsed_result: Dict[str, Any]
    model: str
    tokens_used: Optional[int] = None
    processing_time_ms: Optional[float] = None


@dataclass
class AnalysisConfig:
    """Configuration for VLM analysis.
    
    Attributes:
        frame_count: Number of frames to sample for analysis
        frame_interval: Seconds between frame samples
        threshold: Confidence threshold for tag detection (0-1)
        return_timestamps: Whether to return timestamp information
        return_confidence: Whether to return confidence scores
        max_tokens: Maximum tokens for VLM response
        timeout: Request timeout in seconds
    """
    
    frame_count: int = 20
    frame_interval: float = 2.0
    threshold: float = 0.5
    return_timestamps: bool = True
    return_confidence: bool = True
    max_tokens: int = 4096
    timeout: float = 120.0


class VLMEngine(ABC):
    """Abstract base class for VLM engines.
    
    All VLM engine implementations must inherit from this class
    and implement the analyze_frames method.
    """
    
    def __init__(self, model: str, config: AnalysisConfig):
        """Initialize the VLM engine.
        
        Args:
            model: Model identifier string
            config: Analysis configuration
        """
        self.model = model
        self.config = config
        self._initialized = False
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the engine (validate API keys, etc.)."""
        pass
    
    @abstractmethod
    async def analyze_frames(
        self,
        frames: List[Image.Image],
        prompt: str,
    ) -> VLMResponse:
        """Analyze frames and return structured response.
        
        Args:
            frames: List of PIL Image objects to analyze
            prompt: Analysis prompt text
            
        Returns:
            VLMResponse with parsed results
        """
        pass
    
    def _encode_frame_to_base64(
        self,
        frame: Image.Image,
        format: str = "JPEG",
        quality: int = 85,
    ) -> str:
        """Encode a PIL Image to base64 string.
        
        Args:
            frame: PIL Image to encode
            format: Image format (JPEG, PNG)
            quality: JPEG quality (0-100)
            
        Returns:
            Base64 encoded image string with data URI prefix
        """
        buffer = io.BytesIO()
        
        # Convert RGBA to RGB if necessary (for JPEG)
        if format.upper() == "JPEG" and frame.mode in ("RGBA", "P"):
            frame = frame.convert("RGB")
        
        frame.save(buffer, format=format, quality=quality)
        buffer.seek(0)
        
        image_data = buffer.getvalue()
        base64_data = base64.b64encode(image_data).decode("utf-8")
        
        mime_type = f"image/{format.lower()}"
        return f"data:{mime_type};base64,{base64_data}"
    
    async def sample_frames(
        self,
        video_path: Path,
        strategy: str = "uniform",
        count: int = 10,
    ) -> List[Tuple[float, Image.Image]]:
        """Sample frames from video for analysis.
        
        Args:
            video_path: Path to video file
            strategy: Sampling strategy ("uniform", "scene_change", "keyframe")
            count: Number of frames to sample
            
        Returns:
            List of (timestamp, frame) tuples
        """
        from haven_cli.media.frames import extract_frames
        from haven_cli.media.metadata import extract_video_duration
        
        duration = await extract_video_duration(video_path)
        
        if duration <= 0:
            return []
        
        # Calculate timestamps based on strategy
        if strategy == "uniform":
            timestamps = self._calculate_uniform_timestamps(duration, count)
        elif strategy == "scene_change":
            # For now, fall back to uniform (scene detection can be added later)
            timestamps = self._calculate_uniform_timestamps(duration, count)
        elif strategy == "keyframe":
            # For now, fall back to uniform (keyframe extraction can be added later)
            timestamps = self._calculate_uniform_timestamps(duration, count)
        else:
            raise ValueError(f"Unknown sampling strategy: {strategy}")
        
        # Extract frames
        frames = await extract_frames(
            video_path,
            timestamps,
            output_format="RGB",
            width=512,
            height=512,
        )
        
        # Pair timestamps with frames
        result = []
        for ts, frame in zip(timestamps, frames):
            if frame is not None:
                result.append((ts, frame))
        
        return result
    
    def _calculate_uniform_timestamps(
        self,
        duration: float,
        count: int,
    ) -> List[float]:
        """Calculate evenly distributed timestamps.
        
        Args:
            duration: Video duration in seconds
            count: Number of timestamps to generate
            
        Returns:
            List of timestamps in seconds
        """
        if count <= 1:
            return [duration / 2]
        
        # Skip 5% from start and end to avoid title/end cards
        skip_start = duration * 0.05
        skip_end = duration * 0.05
        usable_duration = duration - skip_start - skip_end
        
        if usable_duration <= 0:
            return [duration / 2]
        
        step = usable_duration / count
        return [skip_start + i * step + step / 2 for i in range(count)]


class OpenAIVLMEngine(VLMEngine):
    """OpenAI GPT-4 Vision engine.
    
    Uses OpenAI's GPT-4 Vision API for image analysis.
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4-vision-preview",
        config: Optional[AnalysisConfig] = None,
        base_url: Optional[str] = None,
    ):
        """Initialize OpenAI VLM engine.
        
        Args:
            api_key: OpenAI API key
            model: Model name (gpt-4-vision-preview, gpt-4o, etc.)
            config: Analysis configuration
            base_url: Optional custom API base URL
        """
        super().__init__(model, config or AnalysisConfig())
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"
        self._client: Optional[httpx.AsyncClient] = None
    
    async def initialize(self) -> None:
        """Initialize the HTTP client."""
        if self._initialized:
            return
        
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
        
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.config.timeout,
        )
        
        self._initialized = True
    
    async def analyze_frames(
        self,
        frames: List[Image.Image],
        prompt: str,
    ) -> VLMResponse:
        """Analyze frames using GPT-4 Vision.
        
        Args:
            frames: List of PIL Image objects
            prompt: Analysis prompt
            
        Returns:
            VLMResponse with parsed results
        """
        if not self._initialized:
            await self.initialize()
        
        if not self._client:
            raise RuntimeError("Engine not initialized")
        
        # Encode frames to base64
        image_urls = [
            self._encode_frame_to_base64(frame, format="JPEG", quality=85)
            for frame in frames
        ]
        
        # Build message content
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for url in image_urls:
            content.append({
                "type": "image_url",
                "image_url": {"url": url, "detail": "auto"},
            })
        
        # Make API request
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "max_tokens": self.config.max_tokens,
        }
        
        import time
        start_time = time.time()
        
        try:
            response = await self._client.post(
                "/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            
            processing_time = (time.time() - start_time) * 1000
            
            # Extract response text
            message = data["choices"][0].get("message", {})
            content_text = message.get("content", "")
            
            # Try to parse as JSON
            parsed_result = self._parse_response_content(content_text)
            
            return VLMResponse(
                raw_response=data,
                parsed_result=parsed_result,
                model=self.model,
                tokens_used=data.get("usage", {}).get("total_tokens"),
                processing_time_ms=processing_time,
            )
            
        except httpx.HTTPStatusError as e:
            error_msg = f"OpenAI API error: {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_msg = f"{error_msg} - {error_data.get('error', {}).get('message', '')}"
            except Exception:
                pass
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except Exception as e:
            logger.error(f"Error calling OpenAI API: {e}")
            raise
    
    def _parse_response_content(self, content: str) -> Dict[str, Any]:
        """Parse response content, extracting JSON if present.
        
        Args:
            content: Raw response content
            
        Returns:
            Parsed dictionary
        """
        # Try direct JSON parsing first
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        # Try to extract JSON from markdown code blocks
        import re
        json_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        matches = re.findall(json_pattern, content)
        
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue
        
        # Try to find JSON-like structure
        json_pattern2 = r"\{[\s\S]*\}"
        match = re.search(json_pattern2, content)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        
        # Return as raw text if no JSON found
        return {"raw_text": content}
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            self._initialized = False


class GeminiVLMEngine(VLMEngine):
    """Google Gemini Vision engine.
    
    Uses Google's Gemini API for image analysis.
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-pro-vision",
        config: Optional[AnalysisConfig] = None,
    ):
        """Initialize Gemini VLM engine.
        
        Args:
            api_key: Google API key
            model: Model name (gemini-pro-vision, gemini-1.5-flash, etc.)
            config: Analysis configuration
        """
        super().__init__(model, config or AnalysisConfig())
        self.api_key = api_key
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self._client: Optional[httpx.AsyncClient] = None
    
    async def initialize(self) -> None:
        """Initialize the HTTP client."""
        if self._initialized:
            return
        
        if not self.api_key:
            raise ValueError("Google API key is required")
        
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.config.timeout,
        )
        
        self._initialized = True
    
    async def analyze_frames(
        self,
        frames: List[Image.Image],
        prompt: str,
    ) -> VLMResponse:
        """Analyze frames using Gemini.
        
        Args:
            frames: List of PIL Image objects
            prompt: Analysis prompt
            
        Returns:
            VLMResponse with parsed results
        """
        if not self._initialized:
            await self.initialize()
        
        if not self._client:
            raise RuntimeError("Engine not initialized")
        
        # Encode frames
        parts = [{"text": prompt}]
        for frame in frames:
            image_data = self._encode_frame_to_base64(frame, format="PNG")
            # Remove data URI prefix for Gemini
            if "," in image_data:
                image_data = image_data.split(",", 1)[1]
            
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": image_data,
                }
            })
        
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "maxOutputTokens": self.config.max_tokens,
            },
        }
        
        import time
        start_time = time.time()
        
        try:
            response = await self._client.post(
                f"/models/{self.model}:generateContent",
                json=payload,
                params={"key": self.api_key},
            )
            response.raise_for_status()
            data = response.json()
            
            processing_time = (time.time() - start_time) * 1000
            
            # Extract response text
            content = data.get("candidates", [{}])[0].get("content", {})
            parts = content.get("parts", [])
            text_parts = [p.get("text", "") for p in parts if "text" in p]
            content_text = " ".join(text_parts)
            
            # Try to parse as JSON
            parsed_result = self._parse_response_content(content_text)
            
            return VLMResponse(
                raw_response=data,
                parsed_result=parsed_result,
                model=self.model,
                tokens_used=data.get("usageMetadata", {}).get("totalTokenCount"),
                processing_time_ms=processing_time,
            )
            
        except httpx.HTTPStatusError as e:
            error_msg = f"Gemini API error: {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_msg = f"{error_msg} - {error_data.get('error', {}).get('message', '')}"
            except Exception:
                pass
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except Exception as e:
            logger.error(f"Error calling Gemini API: {e}")
            raise
    
    def _parse_response_content(self, content: str) -> Dict[str, Any]:
        """Parse response content, extracting JSON if present."""
        import re
        
        # Try direct JSON parsing
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        # Try to extract JSON from markdown
        json_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        matches = re.findall(json_pattern, content)
        
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue
        
        # Try to find JSON-like structure
        json_pattern2 = r"\{[\s\S]*\}"
        match = re.search(json_pattern2, content)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        
        return {"raw_text": content}
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            self._initialized = False


class LocalVLMEngine(VLMEngine):
    """Local VLM engine using vLLM, llama.cpp, or compatible APIs.
    
    Connects to local model servers that implement OpenAI-compatible APIs.
    """
    
    def __init__(
        self,
        base_url: str,
        model: str = "local",
        config: Optional[AnalysisConfig] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize Local VLM engine.
        
        Args:
            base_url: Base URL of the local API server (e.g., http://localhost:8000/v1)
            model: Model identifier
            config: Analysis configuration
            api_key: Optional API key (for servers that require it)
        """
        super().__init__(model, config or AnalysisConfig())
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "not-needed"
        self._client: Optional[httpx.AsyncClient] = None
    
    async def initialize(self) -> None:
        """Initialize the HTTP client."""
        if self._initialized:
            return
        
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.config.timeout,
        )
        
        self._initialized = True
    
    async def analyze_frames(
        self,
        frames: List[Image.Image],
        prompt: str,
    ) -> VLMResponse:
        """Analyze frames using local model.
        
        Args:
            frames: List of PIL Image objects
            prompt: Analysis prompt
            
        Returns:
            VLMResponse with parsed results
        """
        if not self._initialized:
            await self.initialize()
        
        if not self._client:
            raise RuntimeError("Engine not initialized")
        
        # Encode frames to base64
        image_urls = [
            self._encode_frame_to_base64(frame, format="JPEG", quality=85)
            for frame in frames
        ]
        
        # Build message content (OpenAI-compatible format)
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for url in image_urls:
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
            })
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "max_tokens": self.config.max_tokens,
        }
        
        import time
        start_time = time.time()
        
        try:
            response = await self._client.post(
                "/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            
            processing_time = (time.time() - start_time) * 1000
            
            # Extract response
            message = data["choices"][0].get("message", {})
            content_text = message.get("content", "")
            
            # Try to parse as JSON
            parsed_result = self._parse_response_content(content_text)
            
            return VLMResponse(
                raw_response=data,
                parsed_result=parsed_result,
                model=self.model,
                tokens_used=data.get("usage", {}).get("total_tokens"),
                processing_time_ms=processing_time,
            )
            
        except httpx.HTTPStatusError as e:
            error_msg = f"Local VLM API error: {e.response.status_code}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except Exception as e:
            logger.error(f"Error calling local VLM API: {e}")
            raise
    
    def _parse_response_content(self, content: str) -> Dict[str, Any]:
        """Parse response content, extracting JSON if present."""
        import re
        
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        json_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        matches = re.findall(json_pattern, content)
        
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue
        
        json_pattern2 = r"\{[\s\S]*\}"
        match = re.search(json_pattern2, content)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        
        return {"raw_text": content}
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            self._initialized = False


def create_vlm_engine(
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    config: Optional[AnalysisConfig] = None,
) -> VLMEngine:
    """Factory function to create VLM engine based on model name.
    
    Args:
        model: Model identifier (e.g., "gpt-4-vision-preview", "gemini-pro-vision")
        api_key: API key for the service
        base_url: Optional custom base URL
        config: Analysis configuration
        
    Returns:
        Configured VLMEngine instance
        
    Raises:
        ValueError: If model type is not recognized
    """
    model_lower = model.lower()
    
    if "gpt" in model_lower or "openai" in model_lower:
        return OpenAIVLMEngine(
            api_key=api_key or "",
            model=model,
            config=config,
            base_url=base_url,
        )
    elif "gemini" in model_lower:
        return GeminiVLMEngine(
            api_key=api_key or "",
            model=model,
            config=config,
        )
    elif "local" in model_lower or "llava" in model_lower or base_url:
        return LocalVLMEngine(
            base_url=base_url or "http://localhost:8000/v1",
            model=model,
            config=config,
            api_key=api_key,
        )
    else:
        # Default to OpenAI-compatible API
        return LocalVLMEngine(
            base_url=base_url or "http://localhost:8000/v1",
            model=model,
            config=config,
            api_key=api_key,
        )
