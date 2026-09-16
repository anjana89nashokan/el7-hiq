import os
import pandas as pd

def load_files_to_markdown(file_names: list[str]) -> str:
    """
    Load Excel or CSV files from a directory, convert them to markdown format,
    and return them as a single concatenated string.
    
    Args:
        file_names: List of file names (Excel or CSV)
        directory: Directory path where files are located (default: current directory)
    
    Returns:
        Single string with all files converted to markdown format
    """
    directory = "./data/" # You can change this to a specific directory if needed
    
    all_markdown = []
    
    for file_name in file_names:
        file_path = os.path.join(directory, file_name)
        
        if not os.path.exists(file_path):
            all_markdown.append(f"## {file_name}\n\n*File not found: {file_path}*\n\n")
            continue
        
        try:
            # Determine file type and read accordingly
            file_ext = os.path.splitext(file_name)[1].lower()
            
            if file_ext == '.csv':
                df_dict = {'Sheet1': pd.read_csv(file_path)}
            elif file_ext in ['.xlsx', '.xls', '.xlsm', '.xlsb']:
                # Read all sheets from Excel file
                df_dict = pd.read_excel(file_path, sheet_name=None)
            else:
                all_markdown.append(f"## {file_name}\n\n*Unsupported file format: {file_ext}*\n\n")
                continue
            
            # Add file header
            all_markdown.append(f"## {file_name}\n\n")
            
            # Convert each sheet/dataframe to markdown
            for sheet_name, df in df_dict.items():
                if len(df_dict) > 1:
                    all_markdown.append(f"### Sheet: {sheet_name}\n\n")
                
                # Convert dataframe to markdown table
                markdown_table = df.to_markdown(index=False)
                all_markdown.append(f"{markdown_table}\n\n")
                
                # Add basic info
                rows, cols = df.shape
                all_markdown.append(f"*{rows} rows × {cols} columns*\n\n")
        
        except Exception as e:
            all_markdown.append(f"## {file_name}\n\n*Error reading file: {str(e)}*\n\n")
    
    return "".join(all_markdown)


if __name__ == "__main__":
    # Example usage
   
    file_names = ["DataDictionary_IBC_M3P_CLM80A_20260226_142436.xlsx", "IBC_mp3 ledger_data dictinoary 1_20260226_144254.xlsx"]  # Replace with your actual file names
    result = load_files_to_markdown(file_names)
    print(result)