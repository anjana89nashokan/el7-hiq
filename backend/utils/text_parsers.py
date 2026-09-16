"""
Text Parsers - Parse text-based data dictionary and profiling outputs
"""

import re
import logging
from typing import List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_datadict_markdown_table(text: str) -> List[Dict[str, Any]]:
    """
    Parse data dictionary from markdown table format.

    Example input:
    "File Name | Field Name | Data Type | Length | Primary Key | Foreign Key | Field Description |
    |----|----|----|----|----|----|----|
    | members | member_id | STRING | 255 | No | No | The unique identifier |"

    Returns:
        List of dictionaries with data dictionary information
    """
    try:
        lines = text.strip().split('\n')

        if len(lines) < 3:
            logger.error("Invalid table format: not enough lines")
            return []

        # Parse header row
        header_line = lines[0].strip()
        headers = [h.strip() for h in header_line.split('|') if h.strip()]

        logger.info(f"Parsed headers: {headers}")

        # Skip separator row (line with |----|----|)
        data_lines = [line for line in lines[2:] if line.strip() and not line.strip().startswith('|---')]

        result = []
        for line in data_lines:
            if not line.strip():
                continue

            # Split by pipe and clean
            values = [v.strip() for v in line.split('|')]
            # Remove empty first/last elements from leading/trailing pipes
            values = [v for v in values if v]

            if len(values) != len(headers):
                logger.warning(f"Row has {len(values)} values but expected {len(headers)}: {line}")
                continue

            # Create dictionary
            row_dict = {}
            for i, header in enumerate(headers):
                row_dict[header] = values[i]

            result.append(row_dict)

        logger.info(f"Parsed {len(result)} rows from data dictionary table")
        return result

    except Exception as e:
        logger.error(f"Error parsing data dictionary markdown table: {e}", exc_info=True)
        return []


def parse_profiling_text(text: str) -> List[Dict[str, Any]]:
    """
    Parse profiling output from natural language text with embedded tables.

    Example input:
    "### *Table: members_06015d74*
    **Data Quality Score: 97.14%**

    | Column | Data Type | Uniqueness | Nulls | Blanks | Insights |
    |---|---|---|---|---|---|
    | member_id | STRING | 93.33% | 0.00% | 0.00% | Near-unique, potential Primary Key. |"

    Returns:
        List of dictionaries with table reference and column analysis
    """
    try:
        result = []

        # Split by table sections using regex
        # Look for "### *Table: table_name*" or "Table: table_name"
        table_pattern = r'(?:###\s*\*?Table:\s*|Table:\s*)([a-zA-Z0-9_]+)\*?'
        table_matches = list(re.finditer(table_pattern, text, re.IGNORECASE))

        if not table_matches:
            logger.warning("No table sections found in profiling text")
            return []

        for i, match in enumerate(table_matches):
            table_name = match.group(1).strip()

            # Get text for this table (from this match to next match or end)
            start_pos = match.end()
            end_pos = table_matches[i + 1].start() if i + 1 < len(table_matches) else len(text)
            table_text = text[start_pos:end_pos]

            # Extract column analysis from markdown table in this section
            column_analysis = _parse_profiling_table(table_text)

            if column_analysis:
                result.append({
                    "table_reference": f"project.dataset.{table_name}",
                    "column_analysis": column_analysis
                })
                logger.info(f"Parsed profiling for table '{table_name}' with {len(column_analysis)} columns")

        return result

    except Exception as e:
        logger.error(f"Error parsing profiling text: {e}", exc_info=True)
        return []


def _parse_profiling_table(table_text: str) -> Dict[str, Dict[str, Any]]:
    """
    Parse the column analysis table within a profiling section.

    Returns:
        Dictionary with column_name -> stats mapping
    """
    try:
        lines = table_text.strip().split('\n')

        # Find the table (starts with | Column | ...)
        table_start = -1
        headers = []

        for i, line in enumerate(lines):
            if '| Column |' in line or '| column |' in line:
                table_start = i
                headers = [h.strip() for h in line.split('|') if h.strip()]
                break

        if table_start == -1:
            return {}

        # Skip separator row
        data_start = table_start + 2

        column_analysis = {}

        for line in lines[data_start:]:
            if not line.strip() or not line.strip().startswith('|'):
                continue

            # Stop if we hit another section
            if line.strip().startswith('#') or line.strip().startswith('**'):
                break

            values = [v.strip() for v in line.split('|') if v.strip()]

            if len(values) < 2:
                continue

            column_name = values[0]

            # Extract statistics from the row
            stats = {}

            # Try to extract sample values from Insights column if available
            insights_text = values[-1] if len(values) > 5 else ""

            # Parse percentages and create stats
            for val in values[1:]:
                if '%' in val:
                    # Try to extract numeric value from percentage
                    try:
                        num = float(val.replace('%', '').strip())
                    except:
                        num = 0

                    # Guess what stat this is based on position or value
                    if 'Uniqueness' in headers:
                        idx = headers.index('Uniqueness') if 'Uniqueness' in headers else -1
                        if idx > 0 and idx < len(values):
                            stats['uniqueness'] = val
                    if 'Nulls' in headers:
                        idx = headers.index('Nulls') if 'Nulls' in headers else -1
                        if idx > 0 and idx < len(values):
                            stats['null_count'] = 0  # Simplified

            # Add some default values for compatibility
            stats['sample_values'] = []
            stats['distinct_count'] = 0
            stats['null_count'] = 0

            # Try to extract insights as sample description
            if insights_text:
                stats['insights'] = insights_text

            column_analysis[column_name] = stats

        return column_analysis

    except Exception as e:
        logger.error(f"Error parsing profiling table: {e}", exc_info=True)
        return {}
