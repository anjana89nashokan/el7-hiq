import * as XLSX from 'xlsx';
import { saveAs } from 'file-saver';

export const exportMappingDataToExcel = (mappingData: any) => {
  if (!mappingData?.column_mappings) {
    throw new Error('No mapping data available to export');
  }

  // Prepare data for Excel export with the same core columns shown in the UI table.
  // Notes:
  //  - Feedback is intentionally excluded (Step 3-only capture field).
  //  - Target metadata fields are taken from the per-row snapshot added in Step 2.
  const excelData = mappingData.column_mappings.map((mapping: any, index: number) => ({
    'Index': index + 1,
    'Database': mapping.target_database || '-',
    'Target_Name': mapping.target_table?.entity_id || '-',
    'Target_Attribute Name': mapping.target_column_name || '-',
    'Logical Attribute Name': mapping.target_logical_attribute_name || '-',
    'Attribute Business Description': mapping.target_attribute_business_description || '-',
    'Data Type': mapping.target_data_type || '-',
    'Default': mapping.target_default || '-',
    'Nullability': mapping.target_nullability === true ? 'Y' : mapping.target_nullability === false ? 'N' : '-',
    'Key (P/F/A)': mapping.target_key || '-',
    'Rule Type': mapping.rule_type || '-',
    'Source Table/File': mapping.source_entity?.entity_id || mapping.source_table?.entity_id || '-',
    'Source Field': Array.isArray(mapping.source_field_names) ? mapping.source_field_names.join(', ') : (mapping.source_field_names || mapping.source_column_name || '-'),
    'Join Conditions(OPTIONAL)': mapping.join_condition?.join_text || '-',
    'Transformation Rules': mapping.transformation_rules_text || '-',
    'Special Considerations(OPTIONAL)': mapping.special_considerations_text || '-',
    'Filters': mapping.row_filter_text || '-'
  }));

  // Create workbook and worksheet
  const workbook = XLSX.utils.book_new();
  const worksheet = XLSX.utils.json_to_sheet(excelData);

  // Add worksheet to workbook
  XLSX.utils.book_append_sheet(workbook, worksheet, 'Mapping Data');

  // Generate Excel file buffer
  const excelBuffer = XLSX.write(workbook, { bookType: 'xlsx', type: 'array' });

  // Create blob and save file
  const blob = new Blob([excelBuffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  const fileName = `mapping-data-${new Date().toISOString().split('T')[0]}.xlsx`;
  
  saveAs(blob, fileName);
};
