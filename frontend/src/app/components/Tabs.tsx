"use client";

import * as React from "react";
import { useRef, useEffect } from "react";

interface TabsProps {
  value: string;
  onValueChange: (value: string) => void;
  children: React.ReactNode;
  className?: string;
}

export function Tabs({ value, onValueChange, children, className }: TabsProps) {
  return (
    <div className={"flex flex-col " + className}>
      {React.Children.map(children, (child) =>
        React.isValidElement(child)
          ? React.cloneElement(child as React.ReactElement<any>, {
              activeValue: value,
              onChange: onValueChange,
            })
          : child
      )}
    </div>
  );
}

interface TabsListProps {
  children: React.ReactNode;
  className?: string;
  activeValue?: string;
  onChange?: (value: string) => void;
}

export function TabsList({ children, className, activeValue, onChange }: TabsListProps) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const list = listRef.current;
    if (!list || !activeValue) return;
    const activeBtn = list.querySelector<HTMLButtonElement>(`[data-value="${activeValue}"]`);
    if (activeBtn) {
      activeBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }
  }, [activeValue]);

  return (
    <div ref={listRef} className={"flex border-b border-gray-200 overflow-x-auto scrollbar-none [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none] " + className}>
      {React.Children.map(children, (child) =>
        React.isValidElement(child)
          ? React.cloneElement(child as React.ReactElement<any>, {
              activeValue,
              onChange,
            })
          : child
      )}
    </div>
  );
}

interface TabsTriggerProps {
  value: string;
  children: React.ReactNode;
  className?: string;
  activeValue?: string;
  onChange?: (value: string) => void;
  title?: string;
}

export function TabsTrigger({
  value,
  children,
  className,
  activeValue,
  onChange,
  title,
}: TabsTriggerProps) {
  const isActive = activeValue === value;
  return (
    <button
      onClick={() => onChange?.(value)}
      title={title}
      data-value={value}
      className={
        [
          "px-4 py-2 text-sm border-b-2 transition-colors",
          isActive
            ? "border-brand-blue text-brand-darkblue"
            : "border-transparent text-gray-600 hover:text-gray-900 hover:border-gray-300",
          className
        ].filter(Boolean).join(" ")
      }
    >
      {children}
    </button>
  );
}

interface TabsContentProps {
  value: string;
  children: React.ReactNode;
  className?: string;
  activeValue?: string;
}

export function TabsContent({ value, children, className, activeValue }: TabsContentProps) {
  if (activeValue !== value) return null;
  return (
    <div className={"flex-1" + (className ? " " + className : "")}>
      {children}
    </div>
  );
}
