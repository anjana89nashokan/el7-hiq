import axios from "axios";
import type { DashboardStats } from "../interfaces/types";
import axiosInstance from "../utils/axios-interceptor";

class DashboardService {
  static async getDashboardDetails(): Promise<DashboardStats> {
    try {
      const response = await axiosInstance.get<DashboardStats>(
        "/sessions/dashboarddetails",
      );
      if (!response.data || Object.keys(response.data).length === 0) {
        throw new Error("No dashboard data available.");
      }
      return response.data;
    } catch (err) {
      if (err instanceof Error) {
        const msg = err.message;
        if (msg.includes("password authentication failed") || msg.includes("connection to server")) {
          throw new Error("Database connection failed. Please contact your administrator.");
        }
        throw err;
      }
      if (axios.isAxiosError(err)) {
        if (!err.response) throw new Error("Network error. Please check your connection.");
        throw new Error(`Server error: ${err.response.status}`);
      }
      throw new Error("An unexpected error occurred.");
    }
  }
}

export default DashboardService;