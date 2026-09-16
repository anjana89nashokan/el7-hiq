import React, { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {formatToLocalDateTime } from "../../utils/dateFormatter";
import type { UserActivity } from "../../interfaces/types";

interface UserActivityListProps {
  activities: UserActivity[];
  title: string;
}

const ITEMS_PER_PAGE = 5;

export const UserList: React.FC<UserActivityListProps> = ({ activities, title }) => {
  const [currentPage, setCurrentPage] = useState(1);

  const totalPages = Math.ceil(activities.length / ITEMS_PER_PAGE);
  const startIndex = (currentPage - 1) * ITEMS_PER_PAGE;
  const currentData = activities.slice(startIndex, startIndex + ITEMS_PER_PAGE);

  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm flex flex-col h-full min-h-[450px]">
      {/* Header */}
      <div className="p-6 border-b border-gray-100 flex justify-between items-center">
        <div>
          <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-[0.2em]">
            {title}
          </h4>
        </div>
      </div>

      {/* Scrollable List Area */}
      <div className="flex-grow overflow-y-auto custom-scrollbar p-2">
        <table className="w-full text-left">
          <thead className="sticky top-0 bg-white z-10">
            <tr className="border-b border-gray-50">
              <th className="px-4 py-3 text-[10px] font-bold text-gray-400 uppercase">
                User
              </th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-400 uppercase">
                Session Count
              </th>
              <th className="px-4 py-3 text-[10px] font-bold text-gray-400 uppercase text-right">
                Last Activity
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {currentData.map((user, idx) => (
              <tr
                key={`${user.user_key}-${idx}`}
                className="hover:bg-gray-50/50 transition-colors"
              >
                <td className="px-4 py-4">
                  <div className="flex flex-col">
                    <span className="text-sm font-bold text-gray-800 tracking-tight">
                      {user.user_key}
                    </span>
                    <span className="text-[10px] text-gray-400 truncate max-w-[150px]">
                      {user.user_email}
                    </span>
                  </div>
                </td>
                <td className="px-4 py-4 text-center">
                  <span className="inline-flex items-center justify-center px-2.5 py-1 text-xs font-black text-brand-darkblue bg-brand-surface rounded-full min-w-[32px]">
                    {user.session_count}
                  </span>
                </td>
                <td className="px-4 py-4 text-right">
                  <span className="text-[11px] font-bold text-brand-darkblue bg-brand-surface px-2 py-1 rounded">
                    {formatToLocalDateTime(user.last_activity)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {activities.length === 0 && (
          <p className="text-center py-10 text-xs text-gray-400 italic">
            No users found
          </p>
        )}
      </div>

      {/* Footer Pagination */}
      <div className="p-4 border-t border-gray-100 flex items-center justify-between bg-gray-50/50 rounded-b-xl">
        <span className="text-[10px] font-black text-gray-400 uppercase">
          {currentPage} / {totalPages || 1}
        </span>
        <div className="flex gap-1">
          <button
            disabled={currentPage === 1}
            onClick={() => setCurrentPage((prev) => prev - 1)}
            className="p-1.5 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
          >
            <ChevronLeft size={14} />
          </button>
          <button
            disabled={currentPage === totalPages || totalPages === 0}
            onClick={() => setCurrentPage((prev) => prev + 1)}
            className="p-1.5 rounded border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
};
