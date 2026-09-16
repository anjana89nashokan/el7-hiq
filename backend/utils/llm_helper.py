from google import genai
from config.settings import config
from typing import Dict
from google.genai import types
from pydantic import BaseModel, Field
import json
from .rate_limiter import RateLimiter

class OutputFormat(BaseModel):
    text_response: str = Field(description="A markdown text response.")
    tool_response: dict = Field(description="The raw response from any tools used.")



class GoogleGeminiClient:
    
    def __init__(self):
        """
        Initialize Google Gemini client.
        
        Args:
            api_key: Google API key for Gemini
            model: Model name (e.g., 'gemini-pro', 'gemini-1.5-pro', 'gemini-1.5-flash')
        """

        self.agent_model = config.AGENT_MODEL
        self.project = config.GOOGLE_CLOUD_PROJECT
        self.location = config.GOOGLE_CLOUD_LOCATION
        self.model_name = config.AGENT_MODEL
        self.rate_limiter = RateLimiter()

        self.prompt = """

You are given **unstructured or malformed data**.
Your task is to **analyze, clean, and reformat** it into a valid JSON object with the following structure:

```json
{
  "text_response": string,        // The textual response, in Markdown format if applicable
  "tool_response": object         // The structured tool output
}
```

---

### **Your Tasks**

1. **Parse the input** and extract the three main fields:

   * `text_response`
   * `tool_response`

2. **Detect the stage automatically** from the input content.
   Possible stages include:

   * `Metadata Template`
   * `Data Dictionary`
   * `Data Anomaly Analysis`
   * `Relationship Analysis`

3. **Format the output** based on the detected stage, following the schemas below.

---

### **Schemas by Stage**

#### **Metadata Template**

```json
"tool_response": {
  "column_level_mapping": [
    {
      "template_column": "Attribute Name",
      "source": "datadict",
      "field": "Field Name",
      "value": "ACTUAL_FIELD_VALUE",
      "transform": "none",
      "reasoning": "Direct match from data dictionary.",
      "confidence": "high",
      "profiling_summary": {
        "null_pct": 0.0,
        "cardinality": 1500,
        "top_values": ["val1", "val2"],
        "suggested_data_type": "STRING",
        "format": "none"
      }
    }
  ],
  "file_specs_mapping": [],
  "relationship_analysis": [],
  "unmapped_columns": [],
  "notes": "",
  "store_for_next_agent": true
}
```

---

#### **Data Dictionary**

```json
"tool_response": {
  "result": [
    {
      "file_name": "",
      "field_name": "",
      "data_type": "",
      "field_length": 0,
      "primary_key": "",
      "foreign_key": ""
    }
  ]
}
```

---

#### **Data Anomaly Analysis**

```json
"tool_response": {
  "data_anomaly_analysis_tool_response": {
    "status": "",
    "sensitivity_level": "",
    "analysis_timestamp": 0,
    "processing_mode": "",
    "tables_analyzed": 0,
    "processing_stats": {
      "anomaly_categories_detected": 0,
      "total_anomalies_detected": 0,
      "tables_processed": 0,
      "total_processing_time": 0.0
    },
    "summary_statistics": {
      "total_tables_analyzed": 0,
      "total_anomalies": 0,
      "overall_data_quality_score": 0.0,
      "anomaly_categories": {},
      "severity_distribution": {
        "low": 0,
        "medium": 0,
        "high": 0
      }
    },
    "table_anomaly_reports": {
      "<dynamic_table_name>": {
        "table_name": "",
        "table_reference": "",
        "total_anomalies_found": 0,
        "anomaly_summary": {
          "columns_with_anomalies": 0,
          "total_anomaly_types": 0,
          "anomaly_types": {},
          "data_quality_score": 0.0,
          "severity_distribution": {
            "low": 0,
            "medium": 0,
            "high": 0
          }
        },
        "column_anomalies": {},
        "table_level_anomalies": []
      }
    }
  }
}
```

---

#### **Relationship Analysis**

```json
"tool_response": {
  "relationship_analysis_tool_response": {
    "processing_mode": "",
    "status": "",
    "processing_stats": {
      "relationships_found": 0,
      "tables_processed": 0,
      "total_processing_time": 0.0
    },
    "cross_table_relationships": [],
    "tables_analyzed": 0,
    "analysis_timestamp": 0,
    "analysis_depth": "",
    "table_details": {}
  }
}
```

---

#### **Metadata Fill**

```json
"tool_response": {
  "message": "string",
  "metadata_table_id": "string",
  "filespecs_table_id": "string"
}
```

---

### **Guidelines**

1. **Detect the stage** by inspecting keywords or structure (e.g., "mapping" → Metadata Template, "file_name" → Data Dictionary, "anomaly" → Data Anomaly Analysis, "relationship" → Relationship Analysis, "metadata_table_id" or "filespecs_table_id" → Metadata Fill).
2. If you encounter `"Malformed function call"` or any `"error"` message, **ignore it** and extract only the relevant information.
3. If a field or list is missing, set it to `null` instead of omitting it.
4. Do **not** add new information or make assumptions beyond the provided data.
5. The final response **must be valid JSON**, with no comments or extra text.

            """
        try:

            self.genai = genai.Client(vertexai=True, location=self.location, project=self.project)
            # vertexai=True, location=self.location, project=self.project

        except Exception as E:
            raise Exception(
                f"Failed to initialize Client: {E}"
            )
    
    def generate(self, stage, data, session_id=None, **kwargs) -> Dict:
        """
        Generate content using Google Gemini.

        Args:
            stage: The generation stage/prompt selector.
            data: The data to send to Gemini.
            session_id: Optional session id (accepted for caller compatibility; not
                required for generation).

        Returns:
            Generated structured output response
        """


        generation_config = {
            "temperature": 0.1,
            "max_output_tokens": 50000
        }
     
        print("-"*200)
        print("Input")
        print(data)
        print("-"*200)
        try:

            # NOTE: We intentionally do NOT pass `response_schema=OutputFormat`.
            # OutputFormat has a free-form `tool_response: dict` field, which
            # google-genai renders with `additionalProperties` — a key the Gemini
            # Developer API (standalone, api-key mode) rejects. `response_mime_type`
            # still forces valid JSON, and the prompt above fully specifies the
            # exact {text_response, tool_response} shape, which we json.loads below.
            generate_content_config = types.GenerateContentConfig(
                **generation_config,
                thinking_config = types.ThinkingConfig(
                    thinking_budget=-1,
                ),
                response_mime_type="application/json",
            )


            # Rate Limiting check
            # Create a dummy content string for token estimation
            input_content_str = f"{self.prompt} \n STAGE: {stage} \n Input Data: {data}"
            estimated_tokens = self.rate_limiter.count_tokens(input_content_str)
            self.rate_limiter.wait_for_availability(estimated_tokens)

            response = self.genai.models.generate_content(
                model=self.model_name,
                contents=f"""
                            {self.prompt} 
                            ---
                            STAGE: {stage}
                            ---
                            Input Data:

                            {data}""",
                config=generate_content_config if generate_content_config else None
            )

            print("+"*200)
            print("Response")
            print(response.text)
            print("+"*200)

            formatted_data = json.loads(response.text)

            return formatted_data
        except Exception as e:
            raise RuntimeError(f"Gemini generation failed: {str(e)}")
        
    def extract_data(self, data: str) -> Dict:
        
       

        generation_config = {
            "temperature": 0.1,
            "max_output_tokens": 50000
        }
     
        print("-"*200)
        print("Input")
        print(data)
        print("-"*200)
        try:

            generate_content_config = types.GenerateContentConfig(
                **generation_config,
                thinking_config = types.ThinkingConfig(
                    thinking_budget=-1,
                ),
            )


            # Rate Limiting check
            input_content_str = f"Extract from the following data... \n Input Data: {data}"
            estimated_tokens = self.rate_limiter.count_tokens(input_content_str)
            self.rate_limiter.wait_for_availability(estimated_tokens)

            response = self.genai.models.generate_content(
                model=self.model_name,
                contents=f"""
                            Extract from the following data the input the source (input data) and extract Data Dictionary and make sure to include any relevant context and data explanatory information.
                            **2. Extract Data Dictionary Metadata:**
   - For **EVERY** column/attribute found, extract the following specific details if available:
     - `Attribute Name` (Physical column name)
     - `Logical Attribute Name` (Business friendly name)
     - `Attribute Description` (Definition)
     - `Data Type` (e.g., Varchar, Integer, Date)
     - `Length` (Max characters)
     - `Precision` (Decimal places)
     - `Format` (e.g., YYYY-MM-DD)
     - `Nullability` (Is it Nullable? Y/N)
     - `Primary Key` (Is it a PK? Y/N)
     - `Foreign Key` (Is it a FK? Y/N)
                            ---
                            Input Data:

                            {data}""",
                config=generate_content_config if generate_content_config else None
            )

            print("+"*200)
            print("Response")
            print(response.text)
            print("+"*200)

            return response.text
        except Exception as e:
            raise RuntimeError(f"Gemini generation failed: {str(e)}")
        
