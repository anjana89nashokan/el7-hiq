export const FIELD_TYPES = ['STRING', 'INT64', 'FLOAT64', 'NUMERIC', 'DATE', 'TIMESTAMP', 'DATETIME', 'BOOL'];

export const TYPE_CATEGORY: Record<string, string> = {
  STRING: 'STRING', BOOL: 'BOOLEAN',
  INT64: 'NUMBER', FLOAT64: 'NUMBER', NUMERIC: 'NUMBER',
  DATE: 'DATE', TIMESTAMP: 'DATE', DATETIME: 'DATE',
};

export const OPERATOR_CONFIG = [
  { operator: '=',           inputType: 'single', dataType: ['STRING', 'NUMBER', 'DATE', 'BOOLEAN'] },
  { operator: '!=',          inputType: 'single', dataType: ['STRING', 'NUMBER', 'DATE'] },
  { operator: '<',           inputType: 'single', dataType: ['NUMBER', 'DATE'] },
  { operator: '>',           inputType: 'single', dataType: ['NUMBER', 'DATE'] },
  { operator: '<=',          inputType: 'single', dataType: ['NUMBER', 'DATE'] },
  { operator: '>=',          inputType: 'single', dataType: ['NUMBER', 'DATE'] },
  { operator: 'IN',          inputType: 'multi',  dataType: ['STRING', 'NUMBER', 'DATE'] },
  { operator: 'NOT IN',      inputType: 'multi',  dataType: ['STRING', 'NUMBER', 'DATE'] },
  { operator: 'LIKE',        inputType: 'single', dataType: ['STRING'] },
  { operator: 'IS NULL',     inputType: 'none',   dataType: ['STRING', 'NUMBER', 'DATE', 'BOOLEAN'] },
  { operator: 'IS NOT NULL', inputType: 'none',   dataType: ['STRING', 'NUMBER', 'DATE', 'BOOLEAN'] },
];

export function getOperatorsForType(type: string) {
  const cat = TYPE_CATEGORY[type] ?? 'STRING';
  return OPERATOR_CONFIG.filter(o => o.dataType.includes(cat));
}

export function getInputType(operator: string) {
  return OPERATOR_CONFIG.find(o => o.operator === operator)?.inputType ?? 'single';
}
