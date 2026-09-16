# utils/semantic_analyzer.py
"""
Semantic Analysis Module for Healthcare Data Profiling
Uses LLM to understand table context and suggest meaningful composite keys
"""

import logging
import json
import re
from typing import Dict, Any, List
from google import genai
from google.genai import types
from config.settings import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def suggest_composite_keys_with_llm(
    table_reference: str,
    column_metadata: Dict[str, Any],
    sample_rows: List[Dict],
    max_composite_size: int
) -> Dict[str, Any]:
    """
    Use LLM to suggest semantically meaningful composite key combinations
    based on table context and sample data.
    
    Args:
        table_reference: BigQuery table reference
        column_metadata: {column_name: {data_type, uniqueness, null_pct, samples}}
        sample_rows: First 10-20 rows of actual data
        max_composite_size: Max columns in composite key (from config)
    
    Returns:
        {
          "table_context": {
            "detected_level": "authorization_level",
            "confidence": 0.9,
            "reasoning": "...",
            "primary_entity": "authorization",
            "business_context": "..."
          },
          "single_key_candidates": ["auth_id", "authorization_number"],
          "two_column_combos": [["col1", "col2"], ...],
          "three_column_combos": [["col1", "col2", "col3"], ...],
          "four_column_combos": [...] // if max_composite_size >= 4
        }
    """
    
    try:
        # Initialize Gemini client (matching FormatDetector pattern)
        client = genai.Client(
            vertexai=True,
            project=config.GOOGLE_CLOUD_PROJECT,
            location=config.GOOGLE_CLOUD_LOCATION
        )
        model = config.AGENT_MODEL
        
        # Prepare prompt
        prompt = _build_llm_prompt(
            table_reference=table_reference,
            column_metadata=column_metadata,
            sample_rows=sample_rows,
            max_composite_size=max_composite_size
        )
        
        logger.info(f"Requesting LLM analysis for table: {table_reference}")
        logger.debug(f"LLM Prompt: {prompt[:500]}...")  # Log first 500 chars
        
        # Call LLM with structured output (matching FormatDetector pattern)
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,  # Low temperature for deterministic output
                    response_mime_type="application/json"
                )
            )
        except (TypeError, AttributeError) as e:
            # Fallback for older SDK versions without response_mime_type
            logger.warning(f"response_mime_type not supported, using fallback: {e}")
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                )
            )
        
        # Extract response text (matching FormatDetector pattern)
        if hasattr(response, 'text'):
            response_text = response.text.strip()
        elif hasattr(response, 'candidates') and len(response.candidates) > 0:
            response_text = response.candidates[0].content.parts[0].text.strip()
        else:
            logger.error(f"Unexpected response format: {type(response)}")
            raise ValueError("Unable to extract text from LLM response")
        
        # Log raw response for debugging
        logger.info(f"Raw LLM response (first 500 chars): {response_text[:500]}")
        
        # Extract JSON from response (handle markdown code blocks)
        json_text = _extract_json(response_text)
        logger.info(f"Extracted JSON (first 500 chars): {json_text[:500]}")
        
        # Parse JSON
        llm_suggestions = json.loads(json_text)
        
        logger.info(f"LLM analysis complete. Context: {llm_suggestions.get('table_context', {}).get('detected_level', 'unknown')}")
        logger.info(f"Suggested combos: {len(llm_suggestions.get('two_column_combos', []))} 2-col, {len(llm_suggestions.get('three_column_combos', []))} 3-col")
        
        return llm_suggestions
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        logger.error(f"Raw response: {response_text[:1000]}")
        # Fallback to statistical approach
        return _fallback_statistical_suggestions(column_metadata, max_composite_size)
        
    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        # Fallback to statistical approach
        return _fallback_statistical_suggestions(column_metadata, max_composite_size)


def _extract_json(text: str) -> str:
    """
    Extract JSON from response text, handling markdown code blocks and extra text
    (Matches FormatDetector pattern)
    
    Args:
        text: Raw response text
    
    Returns:
        Cleaned JSON string
    """
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


def _build_llm_prompt(
    table_reference: str,
    column_metadata: Dict[str, Any],
    sample_rows: List[Dict],
    max_composite_size: int
) -> str:
    """Build the LLM prompt for composite key suggestion"""
    
    # Limit sample rows for token efficiency
    sample_data_preview = sample_rows[:10] if len(sample_rows) > 10 else sample_rows
    
    # Build column summary
    column_summary = []
    for col_name, metadata in column_metadata.items():
        column_summary.append({
            "name": col_name,
            "type": metadata.get("data_type", "UNKNOWN"),
            "uniqueness": f"{metadata.get('uniqueness', 0):.1f}%",
            "null_pct": f"{metadata.get('null_percentage', 0):.1f}%",
            "samples": metadata.get("sample_values", [])[:3]
        })
    
    # Build the four_column part conditionally (to avoid f-string backslash issues)
    four_col_json = ""
    if max_composite_size >= 4:
        four_col_json = ',\n  "four_column_combos": [\n    ["column1", "column2", "column3", "column4"]\n  ]'
    
    prompt = f"""You are a healthcare data expert analyzing table structure for Business System Analysts.

**CONTEXT:**
This is healthcare insurance data. Common data types include:
- Member/Patient level
- Claim level
- Authorization level
- Transaction/Service line level
- Provider level
- Encounter level
- Or any other level that you determine based on the data pattern and business context

**TABLE TO ANALYZE:**
Table: {table_reference}

**COLUMN METADATA:**
{json.dumps(column_summary, indent=2)}

**SAMPLE DATA (first 10 rows):**
{json.dumps(sample_data_preview, indent=2, default=str)}

**YOUR TASK:**
1. **Determine Table Context:**
   - What level/grain is this table? (member-level, claim-level, authorization-level, etc.)
   - What is the primary business entity?
   - What type of healthcare data is this?
   - Provide confidence score (0.0 to 1.0)

2. **Suggest Composite Key Combinations:**
   - Suggest ALL semantically relevant combinations up to {max_composite_size} columns
   - Consider business meaning, not just statistical uniqueness
   - Prioritize combinations that make sense for the detected table level

**RULES FOR COMPOSITE KEY SUGGESTIONS:**
- ✅ DO suggest combinations that match the table grain (e.g., auth_id + line_number for auth detail)
- ✅ DO consider temporal columns (dates) for time-series uniqueness
- ✅ DO prioritize high-cardinality, low-null columns
- ❌ DO NOT mix different grain levels (e.g., member_id + claim_id in auth-level data)
- ❌ DO NOT use name/description/text fields as key components
- ❌ DO NOT suggest obviously non-unique combinations (e.g., first_name + last_name)
- ❌ DO NOT suggest redundant combinations (if single column is 100% unique, don't add more)

**CRITICAL: Return ONLY a valid JSON object. No markdown, no explanations, no code blocks. Just the raw JSON.**

**REQUIRED OUTPUT FORMAT (strict JSON):**
{{
  "table_context": {{
    "detected_level": "authorization_level",
    "confidence": 0.9,
    "primary_entity": "authorization",
    "business_context": "Prior authorization requests for healthcare services",
    "reasoning": "authorization_number has 1:1 cardinality with row count. Multiple rows share same member_id."
  }},
  "single_key_candidates": [
    "authorization_number",
    "auth_id"
  ],
  "two_column_combos": [
    ["authorization_number", "service_line"],
    ["provider_id", "service_date"]
  ],
  "three_column_combos": [
    ["member_id", "provider_id", "service_date"]
  ]{four_col_json}
}}

**IMPORTANT:**
- Return ONLY valid JSON, no markdown formatting
- Suggest ALL relevant combinations, not just top few
- If a single column is 100% unique with 0% nulls, it's likely the primary key - suggest it but don't force composite keys
- Consider the healthcare domain context in your suggestions
- Explain your reasoning clearly in the reasoning field

Return your analysis now:"""
    
    return prompt


def _fallback_statistical_suggestions(
    column_metadata: Dict[str, Any],
    max_composite_size: int
) -> Dict[str, Any]:
    """
    Fallback to statistical approach if LLM fails.
    Uses simple heuristics based on column names and uniqueness.
    """
    
    logger.warning("Using fallback statistical approach for composite key suggestions")
    
    # Find columns with high uniqueness
    high_uniqueness_cols = []
    single_key_candidates = []
    
    for col_name, metadata in column_metadata.items():
        uniqueness = metadata.get("uniqueness", 0)
        null_pct = metadata.get("null_percentage", 0)
        
        if uniqueness >= 95 and null_pct <= 5:
            single_key_candidates.append(col_name)
        
        if uniqueness >= 50 and null_pct <= 20:
            high_uniqueness_cols.append(col_name)
    
    # Generate simple combinations
    two_column_combos = []
    three_column_combos = []
    four_column_combos = []
    
    if len(high_uniqueness_cols) >= 2:
        # Generate 2-column combos (limit to avoid explosion)
        for i in range(min(len(high_uniqueness_cols), 5)):
            for j in range(i + 1, min(len(high_uniqueness_cols), 6)):
                two_column_combos.append([high_uniqueness_cols[i], high_uniqueness_cols[j]])
                if len(two_column_combos) >= 10:
                    break
            if len(two_column_combos) >= 10:
                break
    
    if max_composite_size >= 3 and len(high_uniqueness_cols) >= 3:
        # Generate 3-column combos (limited)
        for i in range(min(len(high_uniqueness_cols), 3)):
            for j in range(i + 1, min(len(high_uniqueness_cols), 4)):
                for k in range(j + 1, min(len(high_uniqueness_cols), 5)):
                    three_column_combos.append([
                        high_uniqueness_cols[i],
                        high_uniqueness_cols[j],
                        high_uniqueness_cols[k]
                    ])
                    if len(three_column_combos) >= 5:
                        break
                if len(three_column_combos) >= 5:
                    break
            if len(three_column_combos) >= 5:
                break
    
    return {
        "table_context": {
            "detected_level": "unknown",
            "confidence": 0.3,
            "primary_entity": "unknown",
            "business_context": "Statistical analysis only - LLM analysis unavailable",
            "reasoning": "Fallback mode: Using statistical heuristics only"
        },
        "single_key_candidates": single_key_candidates[:5],
        "two_column_combos": two_column_combos,
        "three_column_combos": three_column_combos,
        "four_column_combos": four_column_combos if max_composite_size >= 4 else []
    }


def _merge_pk_candidates(
    column_analysis: Dict[str, Any],
    llm_single_key_candidates: List[str]
) -> List[Dict[str, Any]]:
    """
    Merge statistical analysis with LLM suggestions for primary key candidates.
    
    Args:
        column_analysis: Statistical analysis from BigQuery
        llm_single_key_candidates: Single column candidates from LLM
    
    Returns:
        List of ranked primary key candidates with metadata
    """
    
    pk_candidates = []
    
    for col_name in llm_single_key_candidates:
        if col_name in column_analysis:
            col_stats = column_analysis[col_name]
            
            # Calculate confidence based on uniqueness and nulls
            uniqueness = col_stats.get("uniqueness_percentage", 0)
            null_pct = col_stats.get("null_percentage", 0)
            
            if uniqueness >= 99.5 and null_pct == 0:
                confidence = "HIGH"
            elif uniqueness >= 95 and null_pct <= 5:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            
            pk_candidates.append({
                "column": col_name,
                "confidence": confidence,
                "uniqueness_percentage": uniqueness,
                "null_percentage": null_pct,
                "data_type": col_stats.get("data_type", "UNKNOWN"),
                "primary_key_candidate": col_stats.get("primary_key_candidate", False),
                "distinct_count": col_stats.get("unique_count", 0),
                "sample_values": col_stats.get("distinct_values_sample", [])[:5]
            })
    
    # Sort by confidence and uniqueness
    confidence_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    pk_candidates.sort(
        key=lambda x: (confidence_order.get(x["confidence"], 0), x["uniqueness_percentage"]),
        reverse=True
    )
    
    return pk_candidates