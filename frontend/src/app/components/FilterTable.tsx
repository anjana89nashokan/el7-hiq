import { useDispatch } from 'react-redux';
import { Plus, Trash2 } from 'lucide-react';
import {
  setOverlapFilterRow,
  removeOverlapFilterRow,
} from '../state/reducers/dartReducer';
import type { DynamicFilterRow } from '../state/reducers/dartReducer';
import { FIELD_TYPES, getOperatorsForType, getInputType } from '../config/filterConstants';

interface Props {
  readonly filters: DynamicFilterRow[];
  readonly columns: any[];
  readonly stateKey: string;
  readonly field: 'activeRecordFilters' | 'dynamicFilters';
}

export default function FilterTable({ filters, columns, stateKey, field }: Props) {
  const dispatch = useDispatch();

  if (filters.length === 0) return null;

  return (
    <table className="w-full text-xs border-collapse">
      <thead>
        <tr className="bg-gray-100">
          <th className="border px-2 py-1 text-left">Field Name</th>
          <th className="border px-2 py-1 text-left">Type</th>
          <th className="border px-2 py-1 text-left">Operator</th>
          <th className="border px-2 py-1 text-left">Value</th>
          <th className="border px-2 py-1" />
        </tr>
      </thead>
      <tbody>
        {filters.map((filter, fi) => {
          const ops = getOperatorsForType(filter.type);
          const inputType = getInputType(filter.operator);
          const isDate = ['DATE', 'TIMESTAMP', 'DATETIME'].includes(filter.type);

          return (
            <tr key={fi}>
              <td className="border px-1 py-1">
                <select
                  value={filter.fieldname}
                  onChange={e => {
                    const col = columns.find((c: any) => c.name === e.target.value);
                    dispatch(setOverlapFilterRow({ key: stateKey, field, index: fi, patch: { fieldname: e.target.value, type: col?.data_type ?? filter.type, operator: '', value: [] } }));
                  }}
                  className="w-full px-1 border rounded focus:outline-none focus:ring-1 focus:ring-brand-primary"
                >
                  <option value="">Select</option>
                  {columns.map((c: any) => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select>
              </td>
              <td className="border px-1 py-1">
                <select
                  value={filter.type}
                  onChange={e => dispatch(setOverlapFilterRow({ key: stateKey, field, index: fi, patch: { type: e.target.value, operator: '', value: [] } }))}
                  className="w-full px-1 border rounded focus:outline-none focus:ring-1 focus:ring-brand-primary"
                >
                  <option value="">Select</option>
                  {FIELD_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </td>
              <td className="border px-1 py-1">
                <select
                  value={filter.operator}
                  onChange={e => dispatch(setOverlapFilterRow({ key: stateKey, field, index: fi, patch: { operator: e.target.value, value: [] } }))}
                  className="w-full px-1 border rounded focus:outline-none focus:ring-1 focus:ring-brand-primary"
                >
                  <option value="">Select</option>
                  {ops.map(o => <option key={o.operator} value={o.operator}>{o.operator}</option>)}
                </select>
              </td>
              <td className="border px-1 py-1">
                {inputType !== 'none' && (
                  inputType === 'multi' ? (
                    <div className="space-y-1">
                      {(filter.value.length === 0 ? [''] : filter.value).map((v, vi) => (
                        <div key={vi} className="flex gap-1">
                          <input
                            type={isDate ? 'date' : 'text'}
                            value={v}
                            onChange={e => {
                              const vals = [...(filter.value.length === 0 ? [''] : filter.value)];
                              vals[vi] = e.target.value;
                              dispatch(setOverlapFilterRow({ key: stateKey, field, index: fi, patch: { value: vals } }));
                            }}
                            className="flex-1 px-1 border rounded focus:outline-none focus:ring-1 focus:ring-brand-primary"
                            placeholder={`Value ${vi + 1}`}
                          />
                          {filter.value.length > 1 && (
                            <button type="button" onClick={() => dispatch(setOverlapFilterRow({ key: stateKey, field, index: fi, patch: { value: filter.value.filter((_, i) => i !== vi) } }))} className="text-red-400 hover:text-red-600">
                              <Trash2 size={12} />
                            </button>
                          )}
                        </div>
                      ))}
                      <button type="button" onClick={() => dispatch(setOverlapFilterRow({ key: stateKey, field, index: fi, patch: { value: [...filter.value, ''] } }))} className="flex items-center gap-1 text-font-blue hover:text-font-blue">
                        <Plus size={11} /> Add
                      </button>
                    </div>
                  ) : (
                    <input
                      type={isDate ? 'date' : 'text'}
                      value={filter.value[0] ?? ''}
                      onChange={e => dispatch(setOverlapFilterRow({ key: stateKey, field, index: fi, patch: { value: [e.target.value] } }))}
                      className="w-full px-1 border rounded focus:outline-none focus:ring-1 focus:ring-brand-primary"
                    />
                  )
                )}
              </td>
              <td className="border px-1 py-1 text-center">
                <button type="button" onClick={() => dispatch(removeOverlapFilterRow({ key: stateKey, field, index: fi }))} className="text-red-500 hover:text-red-700">
                  <Trash2 size={14} />
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}


