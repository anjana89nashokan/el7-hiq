import React from 'react';
import type { LucideIcon } from 'lucide-react';

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description: string;
  iconSize?: number;
  iconColor?: string;
  bgColor?: string;
  borderColor?: string;
  titleColor?: string;
  descColor?: string;
  action?: React.ReactNode;
}

export default function EmptyState({
  icon: Icon,
  title,
  description,
  iconSize = 48,
  iconColor = 'text-gray-400',
  bgColor = 'bg-gray-50',
  borderColor = 'border-gray-200',
  titleColor = 'text-gray-600',
  descColor = 'text-gray-500',
  action
}: Readonly<EmptyStateProps>) {
  return (
    <div className={`${bgColor} border ${borderColor} rounded-lg p-6 text-center`}>
      <Icon className={`${iconColor} mx-auto mb-4`} size={iconSize} />
      <h3 className={`text-lg ${titleColor} mb-2`}>{title}</h3>
      <p className={descColor}>{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
