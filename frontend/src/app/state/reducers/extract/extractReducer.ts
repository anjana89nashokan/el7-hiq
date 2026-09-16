import { createSlice, createAsyncThunk, type PayloadAction } from "@reduxjs/toolkit";
import {
  uploadExtractApi,
  fetchBrdInfoApi,
  validateRequirementLayerApi,
  extractFileLayoutApi,
  fileLayoutCheckpointApi,
  approveExtractApi,
  rejectExtractApi,
  extractMetadataApi,
  reviewMetadataApi,
  manualUpdateMetadataApi,
  judgeH1Api,
  judgeDriverApi,
  judgeMetadataApi,
  judgeMappingApi,
  driverMappingApi,
  driverLogicApi,
  driverValidateApi,
  driverApproveApi,
  driverCheckpointApi,
  driverSaveApi,
  extractMappingApi,
  acceptMappingApi,
  fieldHumanCheckpointApi,
  type UploadExtractPayload,
  type BrdInfoResponse,
  type ValidateRequirementLayerResponse,
  type FileLayoutResponse,
  type FileLayoutField,
  type RequirementLayer,
  type ExtractMetadataPayload,
  type ExtractMetadataResponse,
  type ManualUpdateMetadataPayload,
  type FileAttribute,
  type DriverMappingPayload,
  type DriverMapping,
  type DriverLogic,
  type DriverValidation,
  type BsaQuestion,
  type DriverApprovePayload,
  type DriverApproveResponse,
  type DriverCheckpointPayload,
  type DriverCheckpointResponse,
  type JudgeH1Payload,
  type JudgeH1Response,
  type JudgeDriverPayload,
  type JudgeDriverResponse,
  type JudgeMetadataPayload,
  type JudgeMetadataResponse,
  type JudgeMappingPayload,
  type JudgeMappingResponse,
  type DriverSavePayload,
  type ExtractMappingResponse,
  type MappingAcceptPayload,
  type FieldHumanCheckpointPayload,
} from "../../../end-points/extract/extractApi";
import { resetAllState } from "../../actions";

type ReviewStatus = "idle" | "approved" | "rejected";
type UploadStep = "idle" | "uploading" | "extracting" | "validating";

interface ExtractState {
  uploadLoading: boolean;
  uploadStep: UploadStep;
  brdInfoLoading: boolean;
  approveLoading: boolean;
  rejectLoading: boolean;
  fileLayoutLoading: boolean;
  error: string | null;
  uploadSessionId: string | null;
  gcsPrefix: string | null;
  brdInfo: BrdInfoResponse | null;
  validatedLayer: ValidateRequirementLayerResponse | null;
  fileLayoutData: FileLayoutResponse | null;
  editedLayer: RequirementLayer | null;
  reviewStatus: ReviewStatus;
  // Driver Mapping
  driverMappingLoading: boolean;
  driverMappingData: DriverMapping | null;
  driverMappingError: string | null;
  // Driver Logic
  driverLogicLoading: boolean;
  driverLogicData: DriverLogic | null;
  driverLogicBsaQuestions: BsaQuestion[];
  driverLogicError: string | null;
  // Driver Validate
  driverValidateLoading: boolean;
  driverValidateData: DriverValidation | null;
  driverValidateError: string | null;
  // Driver Approve
  driverApproveLoading: boolean;
  driverApproveData: DriverApproveResponse["approved_driver_logic"] | null;
  driverApproveError: string | null;
  driverReviewStatus: "idle" | "approved" | "rejected";
  // Driver Checkpoint
  driverCheckpointLoading: boolean;
  driverCheckpointData: DriverCheckpointResponse | null;
  driverCheckpointError: string | null;
  // Driver Save
  driverSaveLoading: boolean;
  driverSaveError: string | null;
  // Extract Metadata
  metadataLoading: boolean;
  metadataReviewLoading: boolean;
  metadataData: ExtractMetadataResponse | null;
  metadataReviewStatus: ReviewStatus;
  metadataError: string | null;
  brdGcsUri: string | null;
  layoutGcsUri: string | null;
  driverApproveGcsUri: string | null;
  metadataGcsUri: string | null;
  // Extract Mapping
  mappingData: ExtractMappingResponse | null;
  mappingLoading: boolean;
  mappingError: string | null;
  mappingApproved: boolean;
  // Judge H1
  judgeH1Loading: boolean;
  judgeH1Data: JudgeH1Response | null;
  judgeH1Error: string | null;
  // Judge Driver
  judgeDriverLoading: boolean;
  judgeDriverData: JudgeDriverResponse | null;
  judgeDriverError: string | null;
  // Judge Metadata
  judgeMetadataLoading: boolean;
  judgeMetadataData: JudgeMetadataResponse | null;
  judgeMetadataError: string | null;
  // Judge Mapping
  judgeMappingLoading: boolean;
  judgeMappingData: JudgeMappingResponse | null;
  judgeMappingError: string | null;
}

const initialState: ExtractState = {
  uploadLoading: false,
  uploadStep: "idle",
  brdInfoLoading: false,
  approveLoading: false,
  rejectLoading: false,
  fileLayoutLoading: false,
  error: null,
  uploadSessionId: null,
  gcsPrefix: null,
  brdInfo: null,
  validatedLayer: null,
  fileLayoutData: null,
  editedLayer: null,
  reviewStatus: "idle",
  driverMappingLoading: false,
  driverMappingData: null,
  driverMappingError: null,
  driverLogicLoading: false,
  driverLogicData: null,
  driverLogicBsaQuestions: [],
  driverLogicError: null,
  driverValidateLoading: false,
  driverValidateData: null,
  driverValidateError: null,
  driverApproveLoading: false,
  driverApproveData: null,
  driverApproveError: null,
  driverReviewStatus: "idle",
  driverCheckpointLoading: false,
  driverCheckpointData: null,
  driverCheckpointError: null,
  driverSaveLoading: false,
  driverSaveError: null,
  metadataLoading: false,
  metadataReviewLoading: false,
  metadataData: null,
  metadataReviewStatus: "idle",
  metadataError: null,
  brdGcsUri: null,
  layoutGcsUri: null,
  driverApproveGcsUri: null,
  metadataGcsUri: null,
  mappingData: null,
  mappingLoading: false,
  mappingError: null,
  mappingApproved: false,
  judgeH1Loading: false,
  judgeH1Data: null,
  judgeH1Error: null,
  judgeDriverLoading: false,
  judgeDriverData: null,
  judgeDriverError: null,
  judgeMetadataLoading: false,
  judgeMetadataData: null,
  judgeMetadataError: null,
  judgeMappingLoading: false,
  judgeMappingData: null,
  judgeMappingError: null,
};

// ── Thunks ───────────────────────────────────────────────────────────────────

export const runUploadExtract = createAsyncThunk(
  "extract/uploadExtract",
  async (payload: UploadExtractPayload, { dispatch, rejectWithValue }) => {
    try {
      // Step 1: upload files
      dispatch(setUploadStep("uploading"));
      const uploadRes = await uploadExtractApi(payload);
      if (!uploadRes.success) return rejectWithValue("Upload failed");

      // Step 2: extract BRD information
      dispatch(setUploadStep("extracting"));
      const brdInfo = await fetchBrdInfoApi(uploadRes.session_id);
      if (!brdInfo.success) return rejectWithValue("BRD extraction failed");

      // Expose sessionId immediately so the UI can advance to step 2
      // while the two parallel calls below are still in-flight
      dispatch(setUploadSessionId(uploadRes.session_id));

      // Step 3: validate requirement layer + extract file layout in parallel
      dispatch(setUploadStep("validating"));
      const [validatedLayer, fileLayoutData] = await Promise.all([
        validateRequirementLayerApi(brdInfo.session_id),
        extractFileLayoutApi(brdInfo.session_id),
      ]);

      console.log("[runUploadExtract] validatedLayer:", validatedLayer);
      console.log("[runUploadExtract] fileLayoutData:", fileLayoutData);

      const brdGcsUri = validatedLayer.gcs_output_uri ?? "";
      const layoutGcsUri = fileLayoutData.gcs_output_uri ?? "";
      if (brdGcsUri) sessionStorage.setItem("brd_gcs_uri", brdGcsUri);
      if (layoutGcsUri) sessionStorage.setItem("layout_gcs_uri", layoutGcsUri);

      const markdownUploads = brdInfo.markdown_uploads ?? [];
      const toGcs = (path: string) => `gs://bsa-data-map-artifacts/${path}`;
      const brdMarkdownGcsUri = toGcs(markdownUploads.find((u) => u.includes("/markdown_files/brd_")) ?? "");
      const layoutMarkdownGcsUri = toGcs(markdownUploads.find((u) => u.includes("/markdown_files/file_layout_")) ?? "");
      const transcriptMatch = markdownUploads.find((u) => u.includes("/markdown_files/transcript_"));
      const transcriptGcsUri = transcriptMatch ? toGcs(transcriptMatch) : "";
      if (brdMarkdownGcsUri) sessionStorage.setItem("brd_markdown_gcs_uri", brdMarkdownGcsUri);
      if (layoutMarkdownGcsUri) sessionStorage.setItem("layout_markdown_gcs_uri", layoutMarkdownGcsUri);
      if (transcriptGcsUri) sessionStorage.setItem("transcript_gcs_uri", transcriptGcsUri);
      // void markdownUploads;
      // const brdMarkdownGcsUri = "";
      // const layoutMarkdownGcsUri = "";
      // const transcriptGcsUri = "";

      dispatch(runJudgeH1({
        user_id: payload.userId,
        session_id: uploadRes.session_id,
        brd_gcs_uri: brdGcsUri,
        layout_gcs_uri: layoutGcsUri,
        transcript_gcs_uri: transcriptGcsUri,
        brd_markdown_gcs_uri: brdMarkdownGcsUri,
        layout_markdown_gcs_uri: layoutMarkdownGcsUri,
        judge_mode: "pre",
        bsa_rejection_feedback: "",
        revision_number: 0,
      }));

      return { sessionId: uploadRes.session_id, gcsPrefix: uploadRes.gcs_prefix, brdInfo, validatedLayer, fileLayoutData };
    } catch (e: any) {
      console.error("[runUploadExtract] error:", e);
      return rejectWithValue(e.message ?? "Unknown error");
    }
  }
);

export const fileLayoutCheckpoint = createAsyncThunk(
  "extract/fileLayoutCheckpoint",
  async ({ sessionId, fileLayoutTables }: { sessionId: string; fileLayoutTables: Record<string, FileLayoutField[]> }, { rejectWithValue }) => {
    try {
      await fileLayoutCheckpointApi(sessionId, fileLayoutTables);
      return fileLayoutTables;
    } catch (e: any) { return rejectWithValue(e.message); }
  }
);

export const approveExtract = createAsyncThunk(
  "extract/approve",
  async ({ sessionId, requirementLayer }: { sessionId: string; requirementLayer: RequirementLayer }, { rejectWithValue }) => {
    try { return await approveExtractApi(sessionId, requirementLayer); }
    catch (e: any) { return rejectWithValue(e.message); }
  }
);

export const rejectExtract = createAsyncThunk(
  "extract/reject",
  async ({ sessionId, comment }: { sessionId: string; comment: string }, { rejectWithValue }) => {
    try { return await rejectExtractApi(sessionId, comment); }
    catch (e: any) { return rejectWithValue(e.message); }
  }
);

export const runDriverMapping = createAsyncThunk(
  "extract/driverMapping",
  async (payload: DriverMappingPayload, { rejectWithValue }) => {
    try { return await driverMappingApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to generate driver mapping"); }
  }
);

export const runDriverLogic = createAsyncThunk(
  "extract/driverLogic",
  async (payload: DriverMappingPayload, { rejectWithValue }) => {
    try { return await driverLogicApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to generate driver logic"); }
  }
);

export const runDriverValidate = createAsyncThunk(
  "extract/driverValidate",
  async (payload: DriverMappingPayload, { rejectWithValue }) => {
    try { return await driverValidateApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to validate driver"); }
  }
);

export const runDriverApprove = createAsyncThunk(
  "extract/driverApprove",
  async (payload: DriverApprovePayload, { rejectWithValue }) => {
    try { return await driverApproveApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to approve driver"); }
  }
);

export const runDriverCheckpoint = createAsyncThunk(
  "extract/driverCheckpoint",
  async (payload: DriverCheckpointPayload, { rejectWithValue }) => {
    try { return await driverCheckpointApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to submit checkpoint"); }
  }
);

export const runDriverSave = createAsyncThunk(
  "extract/driverSave",
  async (payload: DriverSavePayload, { rejectWithValue }) => {
    try { return await driverSaveApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to save driver logic"); }
  }
);

export const runJudgeH1 = createAsyncThunk(
  "extract/judgeH1",
  async (payload: JudgeH1Payload, { rejectWithValue }) => {
    try { return await judgeH1Api(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to run judge H1"); }
  }
);

export const runJudgeDriver = createAsyncThunk(
  "extract/judgeDriver",
  async (payload: JudgeDriverPayload, { rejectWithValue }) => {
    try { return await judgeDriverApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to run judge driver"); }
  }
);

export const runJudgeMetadata = createAsyncThunk(
  "extract/judgeMetadata",
  async (payload: JudgeMetadataPayload, { rejectWithValue }) => {
    try { return await judgeMetadataApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to run judge metadata"); }
  }
);

export const runJudgeMapping = createAsyncThunk(
  "extract/judgeMapping",
  async (payload: JudgeMappingPayload, { rejectWithValue }) => {
    try { return await judgeMappingApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to run judge mapping"); }
  }
);

export const runExtractMetadata = createAsyncThunk(
  "extract/extractMetadata",
  async (payload: ExtractMetadataPayload, { rejectWithValue }) => {
    try { return await extractMetadataApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to extract metadata"); }
  }
);

export const reviewMetadata = createAsyncThunk(
  "extract/reviewMetadata",
  async (_: void, { getState, rejectWithValue }) => {
    const state = (getState() as { extract: ExtractState }).extract;
    const metadataData = state.metadataData;
    if (!metadataData) return rejectWithValue("No metadata available to submit");
    console.debug("[reviewMetadata] submitting payload:", JSON.stringify(metadataData, null, 2));
    try { return await reviewMetadataApi(metadataData); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to submit review"); }
  }
);

export const runExtractMapping = createAsyncThunk(
  "extract/extractMapping",
  async (sessionId: string, { rejectWithValue }) => {
    try { return await extractMappingApi(sessionId); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to fetch mapping"); }
  }
);

export const acceptMapping = createAsyncThunk(
  "extract/acceptMapping",
  async (payload: MappingAcceptPayload, { rejectWithValue }) => {
    try { return await acceptMappingApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to accept mapping"); }
  }
);

export const runFieldHumanCheckpoint = createAsyncThunk(
  "extract/fieldHumanCheckpoint",
  async (payload: FieldHumanCheckpointPayload & { idx: number }, { rejectWithValue }) => {
    try {
      const row = await fieldHumanCheckpointApi(payload);
      return { row, idx: payload.idx };
    } catch (e: any) { return rejectWithValue(e.message ?? "Failed to update row"); }
  }
);

export const manualUpdateMetadata = createAsyncThunk(
  "extract/manualUpdateMetadata",
  async (payload: ManualUpdateMetadataPayload, { rejectWithValue }) => {
    try { return await manualUpdateMetadataApi(payload); }
    catch (e: any) { return rejectWithValue(e.message ?? "Failed to update metadata"); }
  }
);

/* export const saveExtractEdits = createAsyncThunk(
  "extract/save",
  async ({ sessionId, layer }: { sessionId: string; layer: RequirementLayer }, { rejectWithValue }) => {
    try { await updateExtractApi(sessionId, layer); return layer; }
    catch (e: any) { return rejectWithValue(e.message); }
  }
); */

/* export const updateAndApprove = createAsyncThunk(
  "extract/updateAndApprove",
  async ({ sessionId, layer }: { sessionId: string; layer: RequirementLayer }, { rejectWithValue }) => {
    try {
      await updateExtractApi(sessionId, layer);
      await approveExtractApi(sessionId);
      return layer;
    } catch (e: any) { return rejectWithValue(e.message); }
  }
); */

// ── Slice ────────────────────────────────────────────────────────────────────

const extractSlice = createSlice({
  name: "extract",
  initialState,
  reducers: {
    resetExtract: () => initialState,
    // Restore persisted extract data fields when resuming a saved session.
    // Only data (not loading/error flags) is merged; everything else stays at
    // initial values so the UI renders the saved progress cleanly.
    hydrateExtract(state, action: PayloadAction<Partial<ExtractState>>) {
      Object.assign(state, action.payload);
    },
    setUploadSessionId(state, action: PayloadAction<string>) {
      state.uploadSessionId = action.payload;
    },
    setUploadStep(state, action: PayloadAction<UploadStep>) {
      state.uploadStep = action.payload;
    },
    updateEditedLayer(state, action: PayloadAction<RequirementLayer>) {
      state.editedLayer = action.payload;
    },
    updateMetadataField(state, action: PayloadAction<{ key: string; value: string | null }>) {
      if (state.metadataData) {
        state.metadataData.extracted_filespecs[action.payload.key] = action.payload.value;
      }
    },
    updateFileAttribute(state, action: PayloadAction<{ index: number; field: keyof FileAttribute; value: string | null }>) {
      if (state.metadataData?.extracted_file1) {
        const attr = state.metadataData.extracted_file1.attributes?.[action.payload.index] as any;
        if (attr) attr[action.payload.field] = action.payload.value;
      }
    },
    resetMetadata(state) {
      state.metadataData = null;
      state.metadataReviewStatus = "idle";
      state.metadataError = null;
      state.judgeMetadataLoading = false;
      state.judgeMetadataData = null;
      state.judgeMetadataError = null;
    },
    resetForRetry(state) {
      state.brdInfo = null;
      state.validatedLayer = null;
      state.fileLayoutData = null;
      state.editedLayer = null;
      state.reviewStatus = "idle";
      state.error = null;
      state.judgeH1Loading = false;
      state.judgeH1Data = null;
      state.judgeH1Error = null;
    },
    resetMappingData(state) {
      state.mappingData = null;
      state.mappingLoading = false;
      state.mappingError = null;
      state.mappingApproved = false;
      state.judgeMappingLoading = false;
      state.judgeMappingData = null;
      state.judgeMappingError = null;
    },
    setMappingData(state, action: PayloadAction<ExtractMappingResponse>) {
      state.mappingData = action.payload;
    },
    setMockExtractResult(state, action: PayloadAction<{ session_id: string; validated_requirement_layer: RequirementLayer }>) {
      state.uploadSessionId = action.payload.session_id;
      state.validatedLayer = { validated_requirement_layer: action.payload.validated_requirement_layer } as ValidateRequirementLayerResponse;
      state.editedLayer = action.payload.validated_requirement_layer;
      state.reviewStatus = "idle";
    },
    resetDriverState(state) {
      state.driverMappingData = null;
      state.driverMappingError = null;
      state.driverLogicData = null;
      state.driverLogicBsaQuestions = [];
      state.driverLogicError = null;
      state.driverValidateData = null;
      state.driverValidateError = null;
      state.driverApproveData = null;
      state.driverApproveError = null;
      state.driverReviewStatus = "idle";
      state.judgeDriverLoading = false;
      state.judgeDriverData = null;
      state.judgeDriverError = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(resetAllState, () => initialState)

      // upload → extract brd → validate + file layout
      .addCase(runUploadExtract.pending, (state) => {
        state.uploadLoading = true;
        state.uploadStep = "uploading";
        state.error = null;
        state.reviewStatus = "idle";
      })
      .addCase(runUploadExtract.fulfilled, (state, { payload }) => {
        state.uploadLoading = false;
        state.uploadStep = "idle";
        state.uploadSessionId = payload.sessionId;
        state.gcsPrefix = payload.gcsPrefix;
        state.brdInfo = payload.brdInfo;
        state.validatedLayer = payload.validatedLayer;
        state.fileLayoutData = payload.fileLayoutData;
        state.editedLayer = payload.validatedLayer.validated_requirement_layer ?? payload.brdInfo.validated_requirement_layer ?? null;
        state.brdGcsUri = payload.validatedLayer.gcs_output_uri ?? null;
        state.layoutGcsUri = payload.fileLayoutData.gcs_output_uri ?? null;
        if (state.brdGcsUri) sessionStorage.setItem("brd_gcs_uri", state.brdGcsUri);
        if (state.layoutGcsUri) sessionStorage.setItem("layout_gcs_uri", state.layoutGcsUri);
        console.log("[reducer fulfilled] validatedLayer:", payload.validatedLayer);
        console.log("[reducer fulfilled] fileLayoutData:", payload.fileLayoutData);
        console.log("[reducer fulfilled] editedLayer:", state.editedLayer);
      })
      .addCase(runUploadExtract.rejected, (state, { payload }) => {
        state.uploadLoading = false;
        state.uploadStep = "idle";
        state.error = payload as string;
        console.error("[reducer rejected] error:", payload);
      })

      // file layout checkpoint
      .addCase(fileLayoutCheckpoint.pending, (state) => { state.fileLayoutLoading = true; })
      .addCase(fileLayoutCheckpoint.fulfilled, (state, { payload }) => {
        state.fileLayoutLoading = false;
        if (state.fileLayoutData) {
          state.fileLayoutData = { ...state.fileLayoutData, file_layout_tables: payload };
        }
      })
      .addCase(fileLayoutCheckpoint.rejected, (state, { payload }) => { state.fileLayoutLoading = false; state.error = payload as string; })

      // approve
      .addCase(approveExtract.pending, (state) => { state.approveLoading = true; })
      .addCase(approveExtract.fulfilled, (state, { payload }) => {
        state.approveLoading = false;
        state.reviewStatus = "approved";
        const layer = payload?.validated_requirement_layer?.validated_requirement_layer;
        if (layer) {
          state.editedLayer = layer;
          if (state.validatedLayer) state.validatedLayer = { ...state.validatedLayer, validated_requirement_layer: layer };
        }
      })
      .addCase(approveExtract.rejected, (state, { payload }) => { state.approveLoading = false; state.error = payload as string; })

      // reject
      .addCase(rejectExtract.pending, (state) => { state.rejectLoading = true; })
      .addCase(rejectExtract.fulfilled, (state, { payload }) => { 
        state.rejectLoading = false; 
        if (payload?.validated_requirement_layer) {
          state.validatedLayer = { 
            ...state.validatedLayer!, 
            validated_requirement_layer: payload.validated_requirement_layer 
          };
        }
      })
      .addCase(rejectExtract.rejected, (state, { payload }) => { state.rejectLoading = false; state.error = payload as string; })

      // driver mapping
      .addCase(runDriverMapping.pending, (state) => { state.driverMappingLoading = true; state.driverMappingError = null; state.driverMappingData = null; })
      .addCase(runDriverMapping.fulfilled, (state, { payload }) => {
        state.driverMappingLoading = false;
        state.driverMappingData = payload.driver_mapping;
      })
      .addCase(runDriverMapping.rejected, (state, { payload }) => { state.driverMappingLoading = false; state.driverMappingError = payload as string; })

      // driver logic
      .addCase(runDriverLogic.pending, (state) => { state.driverLogicLoading = true; state.driverLogicError = null; state.driverLogicData = null; })
      .addCase(runDriverLogic.fulfilled, (state, { payload }) => {
        state.driverLogicLoading = false;
        state.driverLogicData = payload.driver_logic;
        state.driverLogicBsaQuestions = payload.bsa_questions ?? [];
      })
      .addCase(runDriverLogic.rejected, (state, { payload }) => { state.driverLogicLoading = false; state.driverLogicError = payload as string; })

      // driver validate
      .addCase(runDriverValidate.pending, (state) => { state.driverValidateLoading = true; state.driverValidateError = null; state.driverValidateData = null; })
      .addCase(runDriverValidate.fulfilled, (state, { payload }) => {
        state.driverValidateLoading = false;
        state.driverValidateData = payload.driver_validation;
      })
      .addCase(runDriverValidate.rejected, (state, { payload }) => { state.driverValidateLoading = false; state.driverValidateError = payload as string; })

      // driver approve
      .addCase(runDriverApprove.pending, (state) => { state.driverApproveLoading = true; state.driverApproveError = null; })
      .addCase(runDriverApprove.fulfilled, (state, { payload }) => {
        state.driverApproveLoading = false;
        state.driverApproveData = payload.approved_driver_logic;
        state.driverReviewStatus = "approved";
        if ((payload as any).gcs_output_uri) {
          state.driverApproveGcsUri = (payload as any).gcs_output_uri;
          sessionStorage.setItem("driver_gcs_uri", (payload as any).gcs_output_uri);
        }
      })
      .addCase(runDriverApprove.rejected, (state, { payload }) => { state.driverApproveLoading = false; state.driverApproveError = payload as string; })

      // driver save (edit)
      .addCase(runDriverSave.pending, (state) => { state.driverSaveLoading = true; state.driverSaveError = null; })
      .addCase(runDriverSave.fulfilled, (state, { payload }) => {
        state.driverSaveLoading = false;
        state.driverLogicData = payload.driver_logic;
      })
      .addCase(runDriverSave.rejected, (state, { payload }) => { state.driverSaveLoading = false; state.driverSaveError = payload as string; })

      // driver checkpoint (reject → re-run)
      .addCase(runDriverCheckpoint.pending, (state) => { state.driverCheckpointLoading = true; state.driverCheckpointError = null; })
      .addCase(runDriverCheckpoint.fulfilled, (state, { payload }) => {
        state.driverCheckpointLoading = false;
        state.driverCheckpointData = payload;
        state.driverReviewStatus = "idle";
        state.driverApproveData = null;
        state.driverApproveError = null;
        // Repopulate all three panels from the checkpoint response
        state.driverMappingData = payload.driver_mapping;
        state.driverLogicData = payload.driver_logic;
        state.driverLogicBsaQuestions = payload.bsa_questions ?? [];
        state.driverValidateData = payload.driver_validation;
      })
      .addCase(runDriverCheckpoint.rejected, (state, { payload }) => { state.driverCheckpointLoading = false; state.driverCheckpointError = payload as string; })

      // extract metadata
      .addCase(runExtractMetadata.pending, (state) => { state.metadataLoading = true; state.metadataError = null; })
      .addCase(runExtractMetadata.fulfilled, (state, { payload }) => {
        state.metadataLoading = false;
        state.metadataData = payload;
        state.metadataReviewStatus = "idle";
      })
      .addCase(runExtractMetadata.rejected, (state, { payload }) => { state.metadataLoading = false; state.metadataError = payload as string; })

      // review metadata
      .addCase(reviewMetadata.pending, (state) => { state.metadataReviewLoading = true; state.metadataError = null; })
      .addCase(reviewMetadata.fulfilled, (state, { payload }) => {
        state.metadataReviewLoading = false;
        if (payload?.status === "saved") {
          state.metadataReviewStatus = "approved";
        }
        if (payload?.gcs_output_uri) {
          state.metadataGcsUri = payload.gcs_output_uri;
          sessionStorage.setItem("metadata_gcs_uri", payload.gcs_output_uri);
        }
      })
      .addCase(reviewMetadata.rejected, (state, { payload }) => { state.metadataReviewLoading = false; state.metadataError = payload as string; })

      // manual update metadata
      .addCase(manualUpdateMetadata.pending, (state) => { state.metadataReviewLoading = true; state.metadataError = null; })
      .addCase(manualUpdateMetadata.fulfilled, (state, { payload }) => {
        state.metadataReviewLoading = false;
        if (payload?.status === "saved") state.metadataReviewStatus = "approved";
      })
      .addCase(manualUpdateMetadata.rejected, (state, { payload }) => { state.metadataReviewLoading = false; state.metadataError = payload as string; })

      // judge H1
      .addCase(runJudgeH1.pending, (state) => { state.judgeH1Loading = true; state.judgeH1Error = null; state.judgeH1Data = null; })
      .addCase(runJudgeH1.fulfilled, (state, { payload }) => { state.judgeH1Loading = false; state.judgeH1Data = payload; })
      .addCase(runJudgeH1.rejected, (state, { payload }) => { state.judgeH1Loading = false; state.judgeH1Error = payload as string; })

      // judge driver
      .addCase(runJudgeDriver.pending, (state) => { state.judgeDriverLoading = true; state.judgeDriverError = null; state.judgeDriverData = null; })
      .addCase(runJudgeDriver.fulfilled, (state, { payload }) => { state.judgeDriverLoading = false; state.judgeDriverData = payload; })
      .addCase(runJudgeDriver.rejected, (state, { payload }) => { state.judgeDriverLoading = false; state.judgeDriverError = payload as string; })

      // judge metadata
      .addCase(runJudgeMetadata.pending, (state) => { state.judgeMetadataLoading = true; state.judgeMetadataError = null; state.judgeMetadataData = null; })
      .addCase(runJudgeMetadata.fulfilled, (state, { payload }) => { state.judgeMetadataLoading = false; state.judgeMetadataData = payload; })
      .addCase(runJudgeMetadata.rejected, (state, { payload }) => { state.judgeMetadataLoading = false; state.judgeMetadataError = payload as string; })

      // judge mapping
      .addCase(runJudgeMapping.pending, (state) => { state.judgeMappingLoading = true; state.judgeMappingError = null; state.judgeMappingData = null; })
      .addCase(runJudgeMapping.fulfilled, (state, { payload }) => { state.judgeMappingLoading = false; state.judgeMappingData = payload; })
      .addCase(runJudgeMapping.rejected, (state, { payload }) => { state.judgeMappingLoading = false; state.judgeMappingError = payload as string; })

      // extract mapping
      .addCase(runExtractMapping.pending, (state) => { state.mappingLoading = true; state.mappingError = null; state.mappingApproved = false; })
      .addCase(runExtractMapping.fulfilled, (state, { payload }) => { state.mappingLoading = false; state.mappingData = payload; })
      .addCase(runExtractMapping.rejected, (state, { payload }) => { state.mappingLoading = false; state.mappingError = payload as string; })

      // accept mapping
      .addCase(acceptMapping.pending, (state) => { state.mappingLoading = true; })
      .addCase(acceptMapping.fulfilled, (state, { payload }) => {
        state.mappingLoading = false;
        if (state.mappingData) state.mappingData.transformation_rules = payload.transformation_rules;
        state.mappingApproved = true;
      })
      .addCase(acceptMapping.rejected, (state, { payload }) => { state.mappingLoading = false; state.mappingError = payload as string; })

      // field human checkpoint
      .addCase(runFieldHumanCheckpoint.fulfilled, (state, { payload }) => {
        if (state.mappingData) {
          state.mappingData.transformation_rules.rows[payload.idx] = payload.row;
        }
      })

      // save edits
      /* .addCase(saveExtractEdits.pending, (state) => { state.actionLoading = true; })
      .addCase(saveExtractEdits.fulfilled, (state, { payload }) => { state.actionLoading = false; state.editedLayer = payload!; })
      .addCase(saveExtractEdits.rejected, (state, { payload }) => { state.actionLoading = false; state.error = payload as string; })
 */
      // update + approve
      // .addCase(updateAndApprove.pending, (state) => { state.actionLoading = true; })
      // .addCase(updateAndApprove.fulfilled, (state, { payload }) => { state.actionLoading = false; state.editedLayer = payload!; state.reviewStatus = "approved"; })
      // .addCase(updateAndApprove.rejected, (state, { payload }) => { state.actionLoading = false; state.error = payload as string; });
  },
});

export const { resetExtract, hydrateExtract, setUploadStep, setUploadSessionId, updateEditedLayer, updateMetadataField, updateFileAttribute, resetMetadata, resetDriverState, resetForRetry, resetMappingData, setMappingData, setMockExtractResult } = extractSlice.actions;
export default extractSlice.reducer;
