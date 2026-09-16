import { useState } from "react";
import DataPreviewTable from "../components/DataPreviewTable";
import Stepper from "../components/Stepper";
import FileUploader from "../components/FileUploader";

type DictionaryRow = {
  FieldName: string;
  Type: string;
  Mode: string;
  Key: string;
  Default: string;
  Description: string;
};

export default function DefineDataDictionary() {
  const [files, setFiles] = useState<File[]>([]);
  
  const dictionary: DictionaryRow[] = [
    {
      FieldName: "PartnerKey",
      Type: "STRING",
      Mode: "REQUIRED",
      Key: "PK",
      Default: "AUTO-GENERATED",
      Description: "Unique identifier assigned to the business partner",
    },
    {
      FieldName: "CustomerAccountNumber",
      Type: "STRING",
      Mode: "REQUIRED",
      Key: "",
      Default: "",
      Description:
        "Account number assigned to the customer by the partner",
    },
    {
      FieldName: "CustomerID",
      Type: "STRING",
      Mode: "REQUIRED",
      Key: "",
      Default: "",
      Description:
        "Internal system-generated unique identifier for the customer",
    },
  ];

  // kept for feature parity: upload parsing still runs, but results not displayed yet
  const handleUpload = async (uploadedFiles: File[]) => {
    if (!uploadedFiles || uploadedFiles.length === 0) {
      return;
    }

    setFiles(prev => [...prev, ...uploadedFiles]);
    const file = uploadedFiles[0];

    // S7756 – using Blob.text() instead of FileReader
    const text = await file.text();

    // flat parsing logic – avoids >4 nesting levels (S2004)
    const rows = text
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.length > 0)
      .map((l) => l.split(","));

    if (rows.length === 0) {
      return;
    }

    const header = rows[0];
    const dataLines = rows.slice(1);

    // parsing side effect intentionally retained (behavior unchanged)
    const parsed = dataLines.map((line) =>
      Object.fromEntries(header.map((h, i) => [h, line[i] ?? ""]))
    );

    // no unused React state → S6754 resolved
    // (no UI consumer in this file, so not storing in useState)
    console.debug('Parsed data:', parsed);
  };

  const handleRemove = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
  };

  return (
    <div>
      <h2 className="text-base font-bold text-brand-darkblue mb-4">
        Define Data Dictionary
      </h2>

      <Stepper />

      <FileUploader 
        onUpload={handleUpload} 
        onRemove={handleRemove}
        multiple={false} 
        files={files}
      />

      <h2 className="text-lg font-semibold text-gray-700 mt-6">
        Preview
      </h2>

      <DataPreviewTable
        columns={["FieldName", "Type", "Mode", "Key", "Default", "Description"]}
        rows={dictionary}
      />
    </div>
  );
}
