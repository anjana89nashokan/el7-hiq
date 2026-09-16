import { ChevronRight, ChevronLeft, RefreshCw } from "lucide-react";
import Button from "./ui/Button";

interface NavigationButtonsProps {
  onPrevious?: () => void;
  onNext?: () => void;
  onRetry?: () => void;
  previousLabel?: string;
  nextLabel?: string;
  showNext?: boolean;
  showRetry?: boolean;
  disabled?: boolean;
  nextDisabled?: boolean;
}

export default function NavigationButtons({
  onPrevious,
  onNext,
  onRetry,
  previousLabel = "Previous",
  nextLabel = "Next",
  showNext = false,
  showRetry = true,
  disabled = false,
  nextDisabled = false,
}: NavigationButtonsProps) {
  return (
    <div className="flex items-center mt-6 gap-3">
      {onPrevious && (
        <Button variant="outline" disabled={disabled} onClick={onPrevious} leftIcon={<ChevronLeft size={16} />}>
          {previousLabel}
        </Button>
      )}
      {/* Retry + Next grouped to the right for consistent placement across steps */}
      <div className="flex items-center gap-3 ml-auto">
        {showRetry && onRetry && (
          <Button variant="outline" disabled={disabled} onClick={onRetry} leftIcon={<RefreshCw size={15} />}>
            Retry
          </Button>
        )}
        {showNext && onNext && (
          <Button variant="primary" disabled={disabled || nextDisabled} onClick={onNext} rightIcon={<ChevronRight size={16} />}>
            {nextLabel}
          </Button>
        )}
      </div>
    </div>
  );
}
