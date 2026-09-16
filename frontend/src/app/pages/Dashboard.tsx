import React, { useEffect, useState } from "react";
import { ActionCard } from "../components/Dashboard/ActionCard";
import type { DashboardStats } from "../interfaces/types";
import DashboardService from "../end-points/dashboardService";
import { UserList } from "../components/Dashboard/UserList";
import { getUserName } from "../utils/userIdentity";
import { sttmNav } from "../utils/sttmRoutes";

const Dashboard: React.FC = () => {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const loadData = async () => {
      try {
        const data = await DashboardService.getDashboardDetails();
        setStats(data);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load dashboard data.",
        );
        console.error(err);
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, []);

  if (loading)
    return (
      <div className="p-10 text-center font-bold text-gray-400">
        Loading Dashboard...
      </div>
    );
  if (error || !stats)
    return (
      <div className="p-10 text-center text-red-500 font-bold">
        {error ?? "No dashboard data available."}
      </div>
    );

  return (
    <div className="min-h-screen bg-brand-light text-slate-900 font-sans pb-12">
      <main className="max-w-7xl mx-auto p-8">
        <h2 className="text-2xl font-bold mb-8 text-brand-darkblue">
          Welcome, {stats.user_name && stats.user_name !== "User" ? stats.user_name : getUserName()}
        </h2>

        <section className="mb-10 bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
          <header className="flex items-center justify-between mb-6">
            <h4 className="text-[12px] font-bold text-gray-400 uppercase">
              Viewing personal statistics for <span className="text-brand-darkblue">{stats.user_name}</span>
            </h4>
          </header>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <ActionCard
              title="Profiling & Mapping"
              sessionCount={stats.session_count}
              buttonText="Start Sourcing"
              to={sttmNav("/upload")}
              progressItems={[
                {
                  label: "Profiling Completion",
                  completed: stats.profiling.completed,
                  pending: stats.profiling.pending,
                },
                {
                  label: "Mapping Completion",
                  completed: stats.mapping.completed,
                  pending: stats.mapping.pending,
                },
              ]}
            />
            <ActionCard
              title="Extract"
              sessionCount={null}
              buttonText="Start Extract"
              to={sttmNav("/extract")}
              progressItems={[
                {
                  label: "Mapping Completion",
                  completed: 0,
                  pending: 0,
                },
              ]}
            />
          </div>
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          <div className="lg:col-span-6">
            <UserList title="All Users Activity - Sourcing " activities={stats.sourcing_users_activity} />
          </div>
          <div className="lg:col-span-6">
            <UserList title="All Users Activity - Extract" activities={stats.extract_users_activity} />
          </div>
        </div>
      </main>
    </div>
  );
};

export default Dashboard;
