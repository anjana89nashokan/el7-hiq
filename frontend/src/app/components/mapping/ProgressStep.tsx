import { Loader2 } from "lucide-react";

interface ProgressStepProps {
  readonly onStartNew: () => void;
}

export default function ProgressStep({ onStartNew }: ProgressStepProps) {
  return (
    <div className="flex flex-col items-center justify-center py-20 animate-in zoom-in duration-300">
      <div className="relative">
        <div className="absolute inset-0 bg-teal-100 rounded-full animate-ping opacity-25"></div>
        <div className="bg-white p-6 rounded-full shadow-lg relative z-10">
          <Loader2 size={48} className="text-font-blue animate-spin" />
        </div>
      </div>
      <h2 className="text-2xl font-bold text-brand-darkblue mt-8">Update in Progress</h2>
      <p className="text-gray-500 mt-2 max-w-md text-center">
        Applying review updates and resolving mapping issues. This may take a few moments.
      </p>

      <div className="mt-12 w-64 h-2 bg-gray-200 rounded-full overflow-hidden">
        <div className="h-full bg-brand-primary animate-[progress_2s_ease-in-out_infinite]" style={{ width: '60%' }}></div>
      </div>

      <button
        onClick={onStartNew}
        className="mt-12 text-font-blue font-medium hover:underline flex items-center gap-2"
      >
        Start New Mapping
      </button>
    </div>
  );
}
