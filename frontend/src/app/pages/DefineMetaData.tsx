export default function DefineMetaData() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-font-blue mb-4">Define Meta Data</h1>
      <p className="text-gray-600">Configure metadata settings for your data mapping.</p>
      <iframe
        src="bigquery_data_profile.html"
        title="BigQuery Data Profile"
        width="100%"
        height="600"
        style={{ border: 'none' }}
      ></iframe>
    </div>
  );
}