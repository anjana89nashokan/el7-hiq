interface LoadingSpinnerProps {
  readonly message?: string;
  readonly size?: 'sm' | 'md' | 'lg';
  readonly fullScreen?: boolean;
}

export default function LoadingSpinner({ message, size = 'md', fullScreen = false }: LoadingSpinnerProps) {
  const sizeClasses = {
    sm: 'h-4 w-4',
    md: 'h-8 w-8',
    lg: 'h-12 w-12'
  };

  const content = (
    <>
      <div className={`animate-spin rounded-full ${sizeClasses[size]} border-b-2 border-brand-primary`}></div>
      {message && <div className="text-center text-font-blue mt-4">{message}</div>}
    </>
  );

  if (fullScreen) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen">
        {content}
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center py-8 gap-3">
      {content}
    </div>
  );
}
