# In server/utils/scoring_utils.py

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def calculate_quality_score(
    column_analysis: Dict, default_value_analysis: Dict = None
) -> Dict[str, Any]:
    """
    (Centralized Production Version) Calculates a comprehensive, multi-dimensional DQS.
    This is the single source of truth for all scoring logic.
    """
    # Temporarily set logger to DEBUG for maximum visibility during testing
    logger.setLevel(logging.DEBUG)
    
    logger.info("--- [SCORING ENGINE] Calculating advanced data quality scores... ---")
    table_dimension_scores = {
        "completeness": [], "uniqueness": [], "distribution": [], "validity": []
    }
    per_column_scores = {}
    weights = {"completeness": 0.40, "uniqueness": 0.25, "distribution": 0.20, "validity": 0.15}

    for col_name, analysis in column_analysis.items():
        if "error" in analysis:
            continue
        
        total_missing_pct = analysis.get("null_percentage", 0) + analysis.get("blank_percentage", 0)
        completeness_score = (1 - (total_missing_pct / 100)) ** 2
        uniqueness_score = analysis.get("uniqueness_percentage", 0) / 100
        
        distribution_score = 1.0
        if default_value_analysis and col_name in default_value_analysis:
            default_pct = default_value_analysis[col_name].get("default_pct", 0)
            if default_pct > 50:
                 distribution_score = 1 - ((default_pct - 50) / 50)
        distribution_score = max(0, distribution_score)

        validity_score = 1.0
        data_type = str(analysis.get("data_type", "")).upper()
        if data_type in ["NUMERICAL", "INTEGER", "INT64", "FLOAT"]:
            min_val, max_val = analysis.get("min_value"), analysis.get("max_value")
            if min_val is not None and max_val is not None and min_val < 0 and max_val > 1000000:
                validity_score = 0.8
        elif data_type in ["TEXT", "STRING"]:
            avg_length = analysis.get("avg_length")
            if avg_length is not None and avg_length == 0: validity_score = 0.3
            elif avg_length is not None and avg_length < 2: validity_score = 0.7
        
        table_dimension_scores["completeness"].append(completeness_score)
        table_dimension_scores["uniqueness"].append(uniqueness_score)
        table_dimension_scores["distribution"].append(distribution_score)
        table_dimension_scores["validity"].append(validity_score)

        column_overall_score = (completeness_score * weights["completeness"] + uniqueness_score * weights["uniqueness"] + distribution_score * weights["distribution"] + validity_score * weights["validity"])
        per_column_scores[col_name] = {
            "overall_score": round(column_overall_score * 100, 2),
            "dimension_scores": {"completeness": round(completeness_score * 100, 2), "uniqueness": round(uniqueness_score * 100, 2), "distribution": round(distribution_score * 100, 2), "validity": round(validity_score * 100, 2)}
        }
        
        logger.debug(
            f"  [SCORE LOG | Column: {col_name}] "
            f"Overall: {per_column_scores[col_name]['overall_score']:.2f} | "
            f"Completeness: {per_column_scores[col_name]['dimension_scores']['completeness']:.2f}, "
            f"Uniqueness: {per_column_scores[col_name]['dimension_scores']['uniqueness']:.2f}, "
            f"Distribution: {per_column_scores[col_name]['dimension_scores']['distribution']:.2f}, "
            f"Validity: {per_column_scores[col_name]['dimension_scores']['validity']:.2f}"
        )

    dimension_averages = {dim: sum(scores) / len(scores) if scores else 1.0 for dim, scores in table_dimension_scores.items()}
    overall_score = sum(dimension_averages[dim] * weight for dim, weight in weights.items())
    
    final_result = {
        "overall_score": round(overall_score * 100, 2),
        "dimension_scores": {dim: round(avg * 100, 2) for dim, avg in dimension_averages.items()},
        "per_column_scores": per_column_scores
    }
    logger.info(f"--- [SCORING ENGINE] Calculation complete. Final table score: {final_result['overall_score']:.2f} ---")
    
    # Diagnostic print to guarantee visibility
    print("\n--- [PRINT DIAGNOSTIC | SCORING ENGINE] Final DQS Object ---")
    print(final_result)
    print("-----------------------------------------------------------\n")
    
    return final_result