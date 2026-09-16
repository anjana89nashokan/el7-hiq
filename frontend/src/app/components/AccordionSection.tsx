import React from 'react';
import { ChevronUp, ChevronDown } from 'lucide-react';

interface AccordionSectionProps {
  title: string;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

export default function AccordionSection({ title, isOpen, onToggle, children }: AccordionSectionProps) {
  return (
    <div className="border border-gray-300 rounded-lg overflow-hidden">
      <div 
        className="flex items-center justify-between px-3 py-2 cursor-pointer bg-gray-50 transition-colors"
        onClick={onToggle}
      >
        <h5 className="text-sm font-semibold text-gray-700">{title}</h5>
        <div className="text-gray-500">
          {isOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </div>
      </div>
      <div className={`overflow-hidden transition-all duration-300 ${isOpen ? 'max-h-none' : 'max-h-0'}`}>
        <div className="p-3 pt-0">
          {children}
        </div>
      </div>
    </div>
  );
}