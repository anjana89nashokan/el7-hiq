import { type ReactNode, useState } from 'react';
import { Bot } from 'lucide-react';
import ChatModal from '../ChatModal';

interface StepWrapperProps {
  readonly title: string;
  readonly children: ReactNode;
  readonly showBotIcon?: boolean;
  readonly onUseResponse?: (response: any, isModified?: boolean) => void;
}

export default function StepWrapper({ title, children, showBotIcon = true, onUseResponse }: StepWrapperProps) {
  const [isChatOpen, setIsChatOpen] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl text-brand-darkblue">{title}</h2>
        {showBotIcon && (
          <button
            type="button"
            className="p-2 rounded-full hover:bg-gray-100 transition-colors cursor-pointer flex items-center gap-2"
            onClick={() => setIsChatOpen(true)}
            aria-label={`Bot info for ${title}`}
          >
            <Bot size={22} className="text-gray-500 hover:text-brand-darkblue" />
            <span>Chat</span>
          </button>
        )}
      </div>
      <div className="bg-white rounded-lg border border-gray-300 p-4">
        {children}
      </div>
      
      <ChatModal
        isOpen={isChatOpen}
        onClose={() => setIsChatOpen(false)}
        stepTitle={title}
        onUseResponse={onUseResponse}
      />
    </div>
  );
}
