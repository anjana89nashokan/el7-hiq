import React from 'react';
import { useNavigate } from 'react-router-dom';

export interface ProgressItem {
  label: string;
  completed: number;
  pending: number;
}

interface ActionCardProps {
  title: string;
  sessionCount: number | null;
  progressItems: ProgressItem[];
  buttonText: string;
  to?: string;
}

export const ActionCard: React.FC<ActionCardProps> = ({ 
  title, sessionCount, progressItems, buttonText, to 
}) => {
  const navigate = useNavigate();

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm flex flex-col justify-between h-full hover:shadow-md transition-shadow">
      <div className="flex justify-between items-start mb-6">
        <h3 className="font-bold text-gray-800 text-sm tracking-tight uppercase">{title}</h3>
        <div className="flex flex-col items-end">
          {sessionCount !== null && (
            <>
              <span className="text-2xl font-black text-gray-800 leading-none">{sessionCount}</span>
              <span className="text-[10px] font-bold text-gray-400 uppercase mt-1">Total</span>
            </>
          )}
        </div>
      </div>
      <div className="flex-grow space-y-5 mb-6">
        {progressItems.map((item, idx) => {
          const total = item.completed + item.pending;
          const rawPercentage = total === 0 ? 0 : (item.completed / total) * 100;
          // Round the calculated percentage to the nearest integer value
          const percentage = Math.round(rawPercentage);
          
          return (
            <div key={idx}>
              <div className="flex justify-between text-[10px] font-bold text-gray-400 mb-1 uppercase tracking-wider">
                <span>{item.label}</span>
                <span>{item.completed}/{total || 0}</span>
              </div>
              <div className="w-full bg-gray-100 h-1.5 rounded-full overflow-hidden">
                <div 
                  className="bg-brand-darkblue h-full transition-all duration-700 ease-out" 
                  style={{ width: `${percentage}%` }} 
                />
              </div>
            </div>
          );
        })}
      </div>

      <button 
        onClick={() => to && navigate(to)}
        className="w-full py-2.5 flex items-center justify-center border-2 border-brand-darkblue text-brand-darkblue font-bold text-xs uppercase tracking-widest rounded-lg hover:bg-brand-darkblue hover:text-white transition-all outline-none cursor-pointer"
      >
        {buttonText}
      </button>
    </div>
  );
};