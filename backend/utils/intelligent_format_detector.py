"""
AI-Powered Format Detection for XML and TXT Files

Uses Gemini model to intelligently detect:
- XML structure and field mappings
- TXT file delimiters and tabular format
"""

import logging
import json
from typing import Dict, Any
from google import genai
from google.genai import types
from config.settings import config
from fastapi import HTTPException

logger = logging.getLogger(__name__)


class FormatDetector:
    """AI-powered format detector for ambiguous file formats"""

    def __init__(self):
        """Initialize Gemini client for format detection"""
        self.client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )
        self.model = config.AGENT_MODEL

    def detect_xml_structure(self, sample: str) -> Dict[str, Any]:
        """
        Detect XML structure using Gemini AI

        Args:
            sample: First 10KB of XML file content

        Returns:
            Dictionary with detected structure:
            {
                "root_element": str,
                "record_path": str,
                "structure_type": "flat|nested|complex",
                "fields": [str],
                "confidence": int (0-100),
                "reasoning": str
            }

        Raises:
            HTTPException: If agent fails or returns invalid response
        """
        prompt = self._build_xml_prompt(sample)

        try:
            response = self._call_agent(prompt)
            return self._validate_xml_response(response)
        except Exception as e:
            logger.error(f"XML format detection failed: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to detect XML format: {str(e)}"
            )

    def detect_txt_format(self, sample: str) -> Dict[str, Any]:
        """
        Detect TXT file format using Gemini AI

        Args:
            sample: First 50 lines of TXT file content

        Returns:
            Dictionary with detected format:
            {
                "is_tabular": bool,
                "delimiter": str,
                "has_header": bool,
                "header_row": int or None,
                "num_columns": int,
                "confidence": int (0-100),
                "reasoning": str
            }

        Raises:
            HTTPException: If agent fails or returns invalid response
        """
        prompt = self._build_txt_prompt(sample)

        try:
            response = self._call_agent(prompt)
            return self._validate_txt_response(response)
        except Exception as e:
            logger.error(f"TXT format detection failed: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to detect TXT format: {str(e)}"
            )

    def _call_agent(self, prompt: str) -> Dict[str, Any]:
        """
        Call Gemini agent with structured JSON output

        Args:
            prompt: Detection prompt

        Returns:
            Parsed JSON response from agent
        """
        try:
            # Note: timeout handled by Vertex AI client configuration
            # Try with response_mime_type first (newer SDK versions)
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,  # Low temperature for consistent structured output
                        # max_output_tokens=1000,
                        response_mime_type="application/json"
                    )
                )
            except (TypeError, AttributeError) as e:
                # Fallback for older SDK versions without response_mime_type
                logger.warning(f"response_mime_type not supported, using fallback: {e}")
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        # max_output_tokens=1000,
                    )
                )

            # Get response text - handle different response formats
            if hasattr(response, 'text'):
                response_text = response.text.strip()
            elif hasattr(response, 'candidates') and len(response.candidates) > 0:
                response_text = response.candidates[0].content.parts[0].text.strip()
            else:
                logger.error(f"Unexpected response format: {type(response)}")
                raise ValueError("Unable to extract text from agent response")

            # Log raw response for debugging
            logger.info(f"Raw agent response (first 500 chars): {response_text[:500]}")

            # Try to extract JSON from response
            json_text = self._extract_json(response_text)

            logger.info(f"Extracted JSON (first 500 chars): {json_text[:500]}")

            # Parse JSON
            parsed_json = json.loads(json_text)
            logger.info(f"Successfully parsed JSON with keys: {list(parsed_json.keys())}")

            return parsed_json

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse agent JSON response: {e}")
            logger.error(f"Response text was: {response_text[:1000]}")
            raise HTTPException(
                status_code=500,
                detail=f"Agent returned invalid JSON response. Error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Agent call failed: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Format detection agent error: {str(e)}"
            )

    def _extract_json(self, text: str) -> str:
        """
        Extract JSON from response text, handling markdown code blocks and extra text

        Args:
            text: Raw response text

        Returns:
            Cleaned JSON string
        """
        import re

        # Remove markdown code blocks if present (```json ... ``` or ``` ... ```)
        if "```" in text:
            # Extract content between code blocks
            match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        # Try to find JSON object boundaries
        # Look for outermost { ... }
        start = text.find('{')
        end = text.rfind('}')

        if start != -1 and end != -1 and end > start:
            text = text[start:end+1]

        return text.strip()

    def _build_xml_prompt(self, sample: str) -> str:
        """Build XML structure detection prompt"""
        return f"""You are an XML structure analyzer. Analyze this XML sample and determine how to parse it as tabular data.

Analyze the following:
1. What is the root element name?
2. What is the record/row element path? (e.g., "catalog/book" or "root/item")
3. Is the structure flat (single-level children) or nested (multi-level)?
4. List all field names that should become DataFrame columns
5. Confidence score (0-100) - how certain are you this is parseable as tabular data?

Rules:
- "flat" = each record has direct child elements only (e.g., <book><title>...</title><author>...</author></book>)
- "nested" = records are nested 1-2 levels deep but still consistent
- "complex" = deeply nested (3+ levels), arrays within records, or inconsistent structure
- If structure is "complex" or non-tabular, set confidence based on parseability

XML Sample:
{sample[:5000]}

CRITICAL: Return ONLY a valid JSON object. No markdown, no explanations, no code blocks. Just the raw JSON.

Required JSON format:
{{
  "root_element": "string",
  "record_path": "string",
  "structure_type": "flat|nested|complex",
  "fields": ["field1", "field2"],
  "confidence": 85,
  "reasoning": "Brief explanation of the structure and why you assigned this confidence"
}}
"""

    def _build_txt_prompt(self, sample: str) -> str:
        """Build TXT format detection prompt"""
        return f"""You are a text file format analyzer. Analyze this TXT file sample and determine if it contains tabular data.

Determine:
1. Is this tabular data (rows and columns)? YES/NO
2. What is the delimiter? Options: '|' (pipe), '\\t' (tab), ',' (comma), ';' (semicolon), 'fixed-width', 'none'
3. Does it have a header row with column names?
4. Which row number is the header (0-based index)? Set to null if no header
5. How many columns are consistently present?
6. Confidence score (0-100) - how certain are you about this analysis?

Rules:
- If not tabular data (logs, narrative text, documents), set is_tabular: false
- If delimiter is fixed-width or none, note that in reasoning
- Confidence should be lower if column counts are inconsistent
- Header row is typically row 0 if present

Sample (first 50 lines):
{sample[:3000]}

CRITICAL: Return ONLY a valid JSON object. No markdown, no explanations, no code blocks. Just the raw JSON.

Required JSON format:
{{
  "is_tabular": true,
  "delimiter": "|",
  "has_header": true,
  "header_row": 0,
  "num_columns": 5,
  "confidence": 90,
  "reasoning": "Brief explanation of detected format and confidence level"
}}"""

    def _validate_xml_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and sanitize XML detection response

        Args:
            response: Raw agent response

        Returns:
            Validated response dictionary

        Raises:
            HTTPException: If response is invalid or missing required fields
        """
        required_fields = ["root_element", "record_path", "structure_type", "fields", "confidence", "reasoning"]

        # Check all required fields present
        missing = [f for f in required_fields if f not in response]
        if missing:
            raise HTTPException(
                status_code=500,
                detail=f"Agent response missing fields: {', '.join(missing)}"
            )

        # Validate structure_type
        if response["structure_type"] not in ["flat", "nested", "complex"]:
            response["structure_type"] = "complex"  # Default to complex if invalid

        # Validate confidence is integer 0-100
        try:
            confidence = int(response["confidence"])
            response["confidence"] = max(0, min(100, confidence))
        except (ValueError, TypeError):
            response["confidence"] = 0

        # Validate fields is a list
        if not isinstance(response["fields"], list):
            response["fields"] = []

        return response

    def _validate_txt_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and sanitize TXT detection response

        Args:
            response: Raw agent response

        Returns:
            Validated response dictionary

        Raises:
            HTTPException: If response is invalid or missing required fields
        """
        required_fields = ["is_tabular", "delimiter", "has_header", "header_row", "num_columns", "confidence", "reasoning"]

        # Check all required fields present
        missing = [f for f in required_fields if f not in response]
        if missing:
            raise HTTPException(
                status_code=500,
                detail=f"Agent response missing fields: {', '.join(missing)}"
            )

        # Validate is_tabular is boolean
        response["is_tabular"] = bool(response["is_tabular"])

        # Validate has_header is boolean
        response["has_header"] = bool(response["has_header"])

        # Validate confidence is integer 0-100
        try:
            confidence = int(response["confidence"])
            response["confidence"] = max(0, min(100, confidence))
        except (ValueError, TypeError):
            response["confidence"] = 0

        # Validate num_columns is integer
        try:
            response["num_columns"] = int(response["num_columns"])
        except (ValueError, TypeError):
            response["num_columns"] = 0

        # Validate header_row is integer or None
        if response["header_row"] is not None:
            try:
                response["header_row"] = int(response["header_row"])
            except (ValueError, TypeError):
                response["header_row"] = None

        return response
