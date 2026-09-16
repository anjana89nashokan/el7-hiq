import { createContext, useContext } from 'react';

export interface ChatContextType {
  isChatOpen: boolean;
  setIsChatOpen: (open: boolean) => void;
}

export const ChatContext = createContext<ChatContextType | undefined>(undefined);

export const useChat = () => {
  const context = useContext(ChatContext);
  if (context === undefined) {
    throw new Error('useChat must be used within a ChatProvider');
  }
  return context;
};