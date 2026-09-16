import type { Activity } from '../../interfaces/types';

export const ActivityTimeline = ({ activities }: { activities: Activity[] }) => (
  <div className="relative pl-6 space-y-6 before:absolute before:left-[11px] before:top-2 before:bottom-2 before:w-[2px] before:bg-gray-100">
    {activities.map((item) => (
      <div key={item.id} className="relative flex justify-between items-center group">
        <div className="absolute -left-[20px] w-3 h-3 rounded-full bg-white border-2 border-darkblue-500 z-10" />
        <p className="text-sm text-gray-700">
          <span className="font-semibold">{item.target}</span> — <span className="text-gray-500">{item.label}</span>
        </p>
        <span className="text-[10px] font-bold text-gray-400 uppercase">{item.time}</span>
      </div>
    ))}
  </div>
);