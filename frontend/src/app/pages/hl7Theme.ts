/** UST brand tokens for HL7 workflow pages (sharp corners, teal accents). */
export const hl7Theme = {
  page: "min-h-screen bg-[#F5F5F5] text-[#4A4A4A] font-sans pb-16",
  container: "max-w-[1200px] mx-auto px-8 py-8",
  eyebrow: "text-[0.8rem] font-bold uppercase tracking-[0.12em] text-[#0097AC]",
  heading: "text-2xl font-bold text-[#212121] leading-tight",
  subtext: "text-sm text-[#4A4A4A] mt-1 leading-relaxed",
  accentRule: "w-16 h-1 bg-[#0097AC] mt-3",
  card: "bg-white border border-[#E0E0E0] border-t-[3px] border-t-[#0097AC]",
  cardHeader: "px-5 py-3 bg-[#F5F5F5] border-b border-[#E0E0E0]",
  cardBody: "p-5",
  statCard: "bg-white border border-[#E0E0E0] border-t-[3px] border-t-[#0097AC] p-5",
  btnPrimary:
    "text-sm font-bold px-8 py-3 bg-[#0097AC] text-white hover:bg-[#006E74] transition-colors",
  btnOutline:
    "text-sm font-bold px-8 py-3 border-2 border-[#006E74] text-[#006E74] bg-transparent hover:bg-[#F5F5F5] transition-colors",
  btnGhost:
    "text-sm font-bold px-4 py-2 border border-[#E0E0E0] bg-white text-[#212121] hover:border-[#0097AC] transition-colors",
  link: "text-[#006E74] hover:underline font-medium",
  tableHead:
    "text-left text-[11px] uppercase tracking-[0.12em] text-[#006E74] font-bold bg-[#F5F5F5]",
  tableRow: "border-t border-[#E0E0E0] hover:bg-[#F5F5F5]",
  table: "w-full table-fixed border-collapse text-sm",
  th: "px-4 py-2.5 text-left align-middle font-bold",
  td: "px-4 py-2.5 align-top",
  tdMiddle: "px-4 py-2.5 align-middle",
  code: "font-mono text-xs",
  codeBlock:
    "p-4 bg-[#212121] text-[#F5F5F5] text-xs font-mono overflow-x-auto max-h-96 border-l-4 border-[#0097AC]",
  note: "bg-[#F5F5F5] border-l-4 border-[#006E74] px-4 py-3 text-sm",
  errorNote: "bg-[#F5F5F5] border-l-4 border-[#EF9A9A] px-3 py-2 text-xs text-[#212121]",
  fieldError: "border-[#EF9A9A] bg-white",
  chipZ: "text-[11px] font-bold px-2 py-0.5 border border-[#0097AC] text-[#006E74] bg-white",
  chipStd: "text-[11px] text-[#4A4A4A]",
  drawer:
    "fixed inset-y-0 right-0 z-50 w-full max-w-md bg-white border-l border-[#E0E0E0] border-t-[3px] border-t-[#0097AC] shadow-[-4px_0_24px_rgba(33,33,33,0.08)] flex flex-col",
  drawerOverlay: "fixed inset-0 z-40 bg-[#212121]/40",
  scrollPanel: "max-h-[min(480px,55vh)] overflow-y-auto overflow-x-auto border border-[#E0E0E0] bg-white",
  chipRow: "px-5 py-3 border-b border-[#E0E0E0] flex gap-2 overflow-x-auto flex-nowrap",
} as const;

export const PAGE_SIZE_OPTIONS = [10, 15, 25] as const;
export type PageSizeOption = (typeof PAGE_SIZE_OPTIONS)[number];
