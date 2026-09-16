import axiosInstance from "../utils/axios-interceptor";

export const getDefaultDatabase = async () => {
  const response = await axiosInstance.get("/data/default-dataset");
  return response.data.dataset_id;
};

export const validateDatasetTables = async (
  datasetId: string,
  tableIds: string[]
) => {
  const response = await axiosInstance.post("messages-strm/validate-dataset-tables", {
    dataset_id: datasetId,
    table_ids: tableIds,
  });
  return response.data;
};

export const getTableSchema = async (tableName: string, datasetId: string) => {
  const response = await axiosInstance.get(
    `/data/table-schema?table_name=${tableName}&dataset_id=${datasetId}`
  );
  return response.data;
};
